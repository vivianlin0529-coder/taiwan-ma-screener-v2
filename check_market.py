#!/usr/bin/env python3
"""
檢查今天是否為台股交易日。
用法: python check_market.py
回傳碼: 0 = 開市, 1 = 休市
"""
import requests, sys
from datetime import date, datetime


def is_twse_trading_day(check_date=None) -> tuple[bool, str]:
    if check_date is None:
        check_date = date.today()

    # 週六日直接排除
    if check_date.weekday() >= 5:
        return False, f'{check_date} 週末，不開市'

    # 查 TWSE 官方休市日曆
    year = check_date.year
    try:
        r = requests.get(
            f'https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule'
            f'?response=json&queryYear={year}',
            timeout=15)
        # API 路徑容錯
        if r.status_code != 200:
            r = requests.get(
                f'https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule'
                f'?response=json&queryYear={year}',
                timeout=15)
        data = r.json().get('data', [])
    except Exception as e:
        print(f'[WARN] 無法取得休市日曆: {e}，預設為開市', flush=True)
        return True, f'{check_date} 無法驗證，預設開市'

    holidays = set()
    for row in data:
        date_str, title, desc = row[0], row[1], row[2]
        if any(kw in desc + title for kw in ['放假', '休市', '停止交易', '不交易']):
            try:
                holidays.add(datetime.strptime(date_str, '%Y-%m-%d').date())
            except Exception:
                pass

    if check_date in holidays:
        # 找出假日名稱
        name = next((r[1] for r in data if r[0] == check_date.strftime('%Y-%m-%d')), '國定假日')
        return False, f'{check_date} {name}，不開市'

    return True, f'{check_date} 正常交易日，開市'


if __name__ == '__main__':
    is_open, msg = is_twse_trading_day()
    print(msg, flush=True)
    sys.exit(0 if is_open else 1)
