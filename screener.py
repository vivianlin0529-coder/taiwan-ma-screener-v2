#!/usr/bin/env python3
"""
Taiwan MA Screener v2
篩選條件（由大到小優先）:
  ①  排除 ETF（代號首碼 "0"）
  ②  排除金融保險業、紡織纖維、通信網路業
  ③  成交量 ≥ 1000 張（流動性過濾）
  ④  均線糾結（三線合一）:
        BIAS1 < 2.4%  → |MA5  - MA10| / MA10
        BIAS2 < 3.0%  → |MA10 - MA20| / MA20
        BIAS3 < 6.4%  → |MA20 - MA60| / MA60
  ⑤  日線縮軌：MA5/10/20 spread < 5%，且連續 3 日收縮
  ⑥  外資連買：連續 3 個交易日外資/陸資淨買超 > 0

用法:
  python screener.py                           # 全量掃描
  python screener.py --preview 50              # 只跑前 50 檔，快速測試
  python screener.py --date 2026-05-06         # 指定截止日期
"""

import twstock
import yfinance as yf
import pandas as pd
import requests, json
from datetime import datetime, timedelta
import time, logging, os, sys

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s',
                    datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

EXCLUDE_GROUPS           = {'金融保險業', '紡織纖維', '通信網路業'}
BIAS1_MAX                = 2.4
BIAS2_MAX                = 3.0
BIAS3_MAX                = 6.4
DAILY_SPREAD_MAX         = 5.0
DAILY_CONTRACT_WINDOW    = 3
VOLUME_MIN               = 1000
FOREIGN_NET_BUY_DAYS     = 3   # ⑥ 外資連買天數
SLEEP                    = 0.35


# ── 0. 全市場股數字典（市值 fallback） ────────────────────────────────────────
def build_shares_dict() -> dict[str, int]:
    shares = {}
    try:
        for d in requests.get(
                'https://openapi.twse.com.tw/v1/opendata/t187ap03_L',
                timeout=20).json():
            s = d.get('已發行普通股數或TDR原股發行股數', '0') or '0'
            shares[d.get('公司代號', '')] = int(s.replace(',', ''))
    except Exception as e:
        log.warning(f'TWSE shares dict: {e}')
    try:
        r = requests.get(
            'https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O',
            timeout=30, stream=True)
        raw = b''
        for chunk in r.iter_content(chunk_size=8192): raw += chunk
        for d in json.loads(raw):
            s = d.get('IssueShares', '0') or '0'
            shares[d.get('SecuritiesCompanyCode', '')] = int(s.replace(',', ''))
    except Exception as e:
        log.warning(f'TPEx shares dict: {e}')
    log.info(f'股數字典: {len(shares)} 筆')
    return shares


# ── ⑥ 外資連買字典 ──────────────────────────────────────────────────────────
def _recent_trading_dates(n: int = 10) -> list[str]:
    """傳回最近 n 個可能的交易日（YYYYMMDD），最新在前，供逐一嘗試"""
    dates = []
    d = datetime.now()
    while len(dates) < n:
        if d.weekday() < 5:   # Mon–Fri
            dates.append(d.strftime('%Y%m%d'))
        d -= timedelta(days=1)
    return dates


def _fetch_twse_foreign_one_day(date_str: str) -> dict[str, int]:
    """
    抓 TWSE T86（三大法人）單日外資買賣超（單位：股）。
    回傳 {code: net_shares}，失敗或當日休市回傳 {}。
    """
    url = (f'https://www.twse.com.tw/rwd/zh/fund/T86'
           f'?response=json&date={date_str}&selectType=ALL')
    try:
        resp = requests.get(url, timeout=20)
        data = resp.json()
        if data.get('stat') != 'OK' or not data.get('data'):
            return {}
        result = {}
        for row in data['data']:
            code = str(row[0]).strip()
            if not (len(code) == 4 and code.isdigit()):
                continue
            try:
                # row[4] = 外陸資買賣超股數（含外資自營）
                net = int(str(row[4]).replace(',', '').replace('+', '').strip())
                result[code] = net
            except Exception:
                pass
        return result
    except Exception as e:
        log.debug(f'TWSE T86 {date_str}: {e}')
        return {}


def _fetch_tpex_foreign_one_day(date_str: str) -> dict[str, int]:
    """
    抓 TPEx 三大法人外資買賣超（單位：股）。
    date_str 格式 YYYYMMDD，轉成 YYYY/MM/DD 供 API 使用。
    """
    ymd = f'{date_str[:4]}/{date_str[4:6]}/{date_str[6:]}'
    url = (f'https://www.tpex.org.tw/openapi/v1/tpex_fund_wforeign'
           f'?date={ymd}')
    try:
        raw = b''
        for chunk in requests.get(url, timeout=30, stream=True).iter_content(8192):
            raw += chunk
        rows = json.loads(raw)
        if not rows:
            return {}
        result = {}
        for r in rows:
            code = str(r.get('SecuritiesCompanyCode', '')).strip()
            if not (len(code) == 4 and code.isdigit()):
                continue
            try:
                net_raw = r.get('ForeignNetBuy', '0') or '0'
                net = int(str(net_raw).replace(',', '').replace('+', '').strip())
                result[code] = net
            except Exception:
                pass
        return result
    except Exception as e:
        log.debug(f'TPEx foreign {date_str}: {e}')
        return {}


def fetch_foreign_netbuy_dict(n_days: int = 3) -> dict[str, list[int]]:
    """
    取最近 n_days 個有資料的交易日，合併 TWSE + TPEx 外資買賣超。
    回傳 {code: [net_d_oldest, ..., net_d_latest]}，
    若某日某股無資料則視為 0（保守：不計入連買）。
    """
    log.info(f'⑥  抓外資連買資料（最近 {n_days} 交易日）…')
    candidate_dates = _recent_trading_dates(n=n_days + 7)  # 多幾天應付假日
    collected: list[tuple[str, dict[str, int]]] = []  # [(date, {code: net})]

    for date_str in candidate_dates:
        if len(collected) >= n_days:
            break
        twse_day = _fetch_twse_foreign_one_day(date_str)
        time.sleep(0.3)
        tpex_day = _fetch_tpex_foreign_one_day(date_str)
        time.sleep(0.3)
        merged = {**tpex_day, **twse_day}   # TWSE 優先覆蓋（以防重複）
        if merged:
            collected.append((date_str, merged))
            log.info(f'   外資 {date_str}: TWSE {len(twse_day)} + TPEx {len(tpex_day)} 股')
        else:
            log.debug(f'   外資 {date_str}: 無資料（可能假日）')

    if len(collected) < n_days:
        log.warning(f'外資資料只取得 {len(collected)} 天（需 {n_days} 天），跳過此篩選條件')
        return {}

    # collected 是 newest-first；反轉為 oldest-first
    collected_asc = list(reversed(collected))  # [oldest, ..., newest]

    # 整合成 {code: [net_oldest, ..., net_newest]}
    all_codes: set[str] = set()
    for _, day_dict in collected_asc:
        all_codes.update(day_dict.keys())

    result: dict[str, list[int]] = {}
    for code in all_codes:
        nets = [day_dict.get(code, 0) for _, day_dict in collected_asc]
        result[code] = nets

    log.info(f'   外資連買字典建立完成：{len(result)} 檔')
    return result


# ── 1. 活躍股清單 ─────────────────────────────────────────────────────────────
def fetch_active_stock_list() -> list[dict]:
    twse_map = {}
    try:
        for r in requests.get(
                'https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL',
                timeout=20).json():
            code = r['Code']
            if len(code) == 4 and code.isdigit():
                twse_map[code] = {
                    'vol_shares': int((r.get('TradeVolume','0') or '0').replace(',','')),
                    'suffix': '.TW'}
    except Exception as e:
        log.error(f'TWSE list: {e}')

    tpex_map = {}
    for attempt in range(3):
        try:
            raw = b''
            for chunk in requests.get(
                    'https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes',
                    timeout=30, stream=True).iter_content(chunk_size=8192):
                raw += chunk
            for r in json.loads(raw):
                code = r['SecuritiesCompanyCode']
                if len(code) == 4 and code.isdigit():
                    tpex_map[code] = {
                        'vol_shares': int((r.get('TradingShares','0') or '0').replace(',','')),
                        'suffix': '.TWO'}
            break
        except Exception as e:
            log.warning(f'TPEx attempt {attempt+1}: {e}')
            time.sleep(2)

    active = {**tpex_map, **twse_map}
    candidates, cnt_etf, cnt_ind, cnt_vol = [], 0, 0, 0
    for code, meta in active.items():
        if code.startswith('0'):      cnt_etf += 1; continue
        info  = twstock.codes.get(code)
        group = (info.group if info else '') or ''
        if group in EXCLUDE_GROUPS:   cnt_ind += 1; continue
        if meta['vol_shares'] / 1000 < VOLUME_MIN: cnt_vol += 1; continue
        candidates.append({'code': code, 'name': info.name if info else code,
                           'group': group, 'suffix': meta['suffix']})

    log.info(f"候選股 {len(candidates)} 檔 "
             f"（排除 ETF:{cnt_etf} / 產業:{cnt_ind} / 量不足:{cnt_vol}）")
    return candidates


# ── 2. 技術指標 ───────────────────────────────────────────────────────────────
def compute_daily_indicators(df: pd.DataFrame) -> pd.DataFrame:
    for p in [5, 10, 20, 60]:
        df[f'MA{p}'] = df['Close'].rolling(p, min_periods=p).mean()
    df['BIAS1'] = (df['MA5']  - df['MA10']).abs() / df['MA10'] * 100
    df['BIAS2'] = (df['MA10'] - df['MA20']).abs() / df['MA20'] * 100
    df['BIAS3'] = (df['MA20'] - df['MA60']).abs() / df['MA60'] * 100
    ma3 = df[['MA5', 'MA10', 'MA20']]
    df['D_spread'] = (ma3.max(axis=1) - ma3.min(axis=1)) / df['Close'] * 100
    return df


def is_daily_contracting(df: pd.DataFrame) -> tuple[bool, float]:
    spread = df['D_spread'].dropna()
    if len(spread) < DAILY_CONTRACT_WINDOW + 1:
        return False, float('nan')
    latest = spread.iloc[-1]
    if latest >= DAILY_SPREAD_MAX:
        return False, latest
    window = spread.iloc[-(DAILY_CONTRACT_WINDOW + 1):]
    return bool(all(window.diff().dropna() <= 0)), latest


# ── 3. 市值 ───────────────────────────────────────────────────────────────────
def get_market_cap_yi(ticker_obj, close, shares_dict, code) -> float | None:
    try:
        mc = ticker_obj.fast_info.market_cap
        if mc and mc > 0:
            return round(mc / 1e8, 1)
    except Exception:
        pass
    shares = shares_dict.get(code, 0)
    if shares > 0 and close > 0:
        return round(shares * close / 1e8, 1)
    return None


# ── 4. 單一股票篩選 ───────────────────────────────────────────────────────────
def screen_one(stock: dict, shares_dict: dict, foreign_dict: dict,
               cutoff: pd.Timestamp | None = None) -> dict | None:
    code, suffix = stock['code'], stock['suffix']
    try:
        # ⑥ 外資連買（先查字典，不過就直接跳出，省 API 時間）
        if foreign_dict:   # 有外資資料才做此過濾
            nets = foreign_dict.get(code, [])
            if len(nets) < FOREIGN_NET_BUY_DAYS:
                return None
            if not all(n > 0 for n in nets[-FOREIGN_NET_BUY_DAYS:]):
                return None

        ticker = yf.Ticker(f"{code}{suffix}")
        df = ticker.history(period='1y', auto_adjust=True)
        if df.empty or len(df) < 65:
            return None
        df.index = pd.to_datetime(df.index).tz_localize(None)
        if cutoff is not None:
            df = df[df.index.normalize() <= cutoff]
        if len(df) < 65:
            return None

        # ③ 成交量
        vol_lots = df['Volume'].iloc[-1] / 1000
        if vol_lots < VOLUME_MIN:
            return None

        # 日線指標
        df = compute_daily_indicators(df)
        df = df.dropna(subset=['MA60', 'BIAS1', 'BIAS2', 'BIAS3', 'D_spread'])
        if len(df) < 5:
            return None
        latest = df.iloc[-1]

        # ④ 均線糾結
        if not (latest['BIAS1'] < BIAS1_MAX and
                latest['BIAS2'] < BIAS2_MAX and
                latest['BIAS3'] < BIAS3_MAX):
            return None

        # ⑤ 日線縮軌
        d_ok, d_spread = is_daily_contracting(df)
        if not d_ok:
            return None

        close = float(latest['Close'])
        mktcap_yi = get_market_cap_yi(ticker, close, shares_dict, code)

        # 外資連買淨量（張）顯示用
        nets = foreign_dict.get(code, [])
        foreign_net_lots = [round(n / 1000, 0) for n in nets[-FOREIGN_NET_BUY_DAYS:]] if nets else []

        return {
            'code':             code,
            'name':             stock['name'],
            'group':            stock['group'],
            'data_date':        str(df.index[-1].date()),
            'close':            round(close, 2),
            'mktcap_yi':        mktcap_yi,
            'vol_k':            int(vol_lots),
            'bias1':            round(float(latest['BIAS1']), 2),
            'bias2':            round(float(latest['BIAS2']), 2),
            'bias3':            round(float(latest['BIAS3']), 2),
            'daily_spread':     round(float(d_spread), 2),
            'foreign_net_3d':   str(foreign_net_lots),   # e.g. "[1200.0, 3400.0, 800.0]"
            'suffix':           suffix,
            'screened_at':      datetime.now().strftime('%Y-%m-%d %H:%M'),
        }
    except Exception as e:
        log.debug(f"[{code}] skip: {e}")
        return None


# ── 5. 主流程 ─────────────────────────────────────────────────────────────────
def run(target_date: str | None = None, max_stocks: int | None = None):
    log.info("=" * 65)
    log.info("  Taiwan MA Screener v2  啟動")
    log.info("=" * 65)
    t0 = datetime.now()

    cutoff = pd.Timestamp(target_date) if target_date else None
    if cutoff:
        log.info(f"[DATE MODE] 截止日期: {target_date}")

    shares_dict  = build_shares_dict()
    foreign_dict = fetch_foreign_netbuy_dict(n_days=FOREIGN_NET_BUY_DAYS)
    candidates   = fetch_active_stock_list()

    if max_stocks:
        candidates = candidates[:max_stocks]
        log.info(f"[PREVIEW] 只掃前 {max_stocks} 檔")

    results, total = [], len(candidates)

    for i, stock in enumerate(candidates):
        if i % 50 == 0 and i > 0:
            log.info(f"  進度 {i}/{total}  通過: {len(results)} 檔")
        result = screen_one(stock, shares_dict=shares_dict,
                            foreign_dict=foreign_dict, cutoff=cutoff)
        if result:
            results.append(result)
            mc_str = f"{result['mktcap_yi']:.0f}億" if result['mktcap_yi'] else "N/A"
            log.info(
                f"✅ {result['code']} {result['name']:<8s} [{result['data_date']}] "
                f"收盤:{result['close']:>8.2f}  市值:{mc_str:>7}  "
                f"量:{result['vol_k']:>6}張  "
                f"BIAS:{result['bias1']:.1f}/{result['bias2']:.1f}/{result['bias3']:.1f}  "
                f"日縮:{result['daily_spread']:.2f}%  "
                f"外資連買:{result['foreign_net_3d']}"
            )
        time.sleep(SLEEP)

    elapsed = (datetime.now() - t0).seconds
    log.info("=" * 65)
    log.info(f"  完成：{len(results)} 檔通過 / {total} 檔掃描 / 耗時 {elapsed}s")
    log.info("=" * 65)

    if results:
        df_out = (pd.DataFrame(results)
                  .sort_values('vol_k', ascending=False)
                  .reset_index(drop=True))
        os.makedirs('output', exist_ok=True)
        label = (target_date.replace('-', '') if target_date
                 else datetime.now().strftime('%Y%m%d_%H%M'))
        csv_path = f'output/screener_{label}.csv'
        df_out.to_csv(csv_path, index=False, encoding='utf-8-sig')
        log.info(f"結果儲存至: {csv_path}")

        cols = ['code', 'name', 'group', 'data_date', 'close', 'mktcap_yi',
                'vol_k', 'bias1', 'bias2', 'bias3', 'daily_spread', 'foreign_net_3d']
        print("\n" + "=" * 110)
        print(f"📊  Taiwan MA Screener v2  資料截至: {target_date or '最新'}  (市值單位: 億元)")
        print("=" * 110)
        print(df_out[cols].to_string(index=False))
        print("=" * 110)
        return df_out

    log.info("今日無股票通過所有篩選條件")
    return pd.DataFrame()


if __name__ == '__main__':
    target_date = None
    max_stocks  = None
    if '--date' in sys.argv:
        idx = sys.argv.index('--date')
        target_date = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else None
    if '--preview' in sys.argv:
        idx = sys.argv.index('--preview')
        max_stocks = int(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) else 30
    run(target_date=target_date, max_stocks=max_stocks)
