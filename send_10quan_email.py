#!/usr/bin/env python3
"""
send_10quan_email.py — 十全奧義選股結果 Email
欄位: 代號|名稱|收盤|漲跌幅|振幅|量(張)|換手率|EPS(季)|P/E|K值(9)|OSC今/昨/前|3日新高
"""

import smtplib, sys, os, glob
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import pandas as pd
from datetime import datetime

SENDER   = os.environ.get('GMAIL_SENDER', 'vivianlin0529@gmail.com')
RECEIVER = os.environ.get('MAIL_TO', SENDER)
APP_PASS = os.environ.get('GMAIL_APP_PASSWORD', '')


def _fmt(val, fmt='.2f', fallback='─'):
    try:
        if val is None or str(val) in ('', 'nan', 'None'):
            return fallback
        return format(float(val), fmt)
    except Exception:
        return fallback


def build_html(df: pd.DataFrame, run_time: datetime) -> str:
    date_str = run_time.strftime('%Y/%m/%d')
    n = len(df)

    logic_html = """
<div style='background:#f0fff4;padding:10px 14px;border-left:4px solid #27AE60;
            margin-bottom:14px;font-size:12px;line-height:1.9'>
<b>十全奧義 選股條件（10 條全通過）</b><br>
1. 近一交易日股價創 <b>3 日來新高</b><br>
2. 近一交易日股價 <b>&gt; 3 元</b><br>
3. 股價振幅 <b>&gt; 1%</b>（當日 High − Low）<br>
4. 成交量 <b>&gt; 200 張</b><br>
5. 週轉率 <b>&gt; 0.5%</b><br>
6. 近 1 季 EPS <b>&gt; 0 元</b>（本益比&gt;0 即獲利為正，TWSE/TPEx API）<br>
7. 本益比 <b>&lt; 20</b>（TWSE/TPEx 本益比 API，開跑前一次抓完）<br>
8. 9K <b>今 &gt; 昨</b>（KD 指標 K 值上升）<br>
9. 輔線值 (5,10,10 XMACD) <b>今 &gt; 昨</b><br>
10. 輔線值 (5,10,10 XMACD) <b>昨 &lt;= 前</b>（OSC V 底反轉）<br>
<span style='color:#888;font-size:11px'>排除 ETF / 金融保險 ・ 排序：成交量由大到小 ・ 週六日資料自動排除</span>
</div>"""

    header = """
<tr style='background:#27AE60;color:white;text-align:center;font-size:12px'>
  <th style='padding:6px 8px'>代號</th>
  <th style='padding:6px 8px'>名稱</th>
  <th style='padding:6px 8px'>收盤</th>
  <th style='padding:6px 8px'>漲跌幅</th>
  <th style='padding:6px 8px'>振幅</th>
  <th style='padding:6px 8px'>量(張)</th>
  <th style='padding:6px 8px'>換手率</th>
  <th style='padding:6px 8px'>P/E</th>
  <th style='padding:6px 8px'>K值(9)<br><small>今/昨</small></th>
  <th style='padding:6px 8px'>OSC(5,10,10)<br><small>今/昨/前</small></th>
  <th style='padding:6px 8px'>3日<br>新高</th>
</tr>"""

    rows_html = ''
    for idx, r in df.iterrows():
        chg   = float(r.get('漲跌幅(%)', 0) or 0)
        chg_c = '#cc0000' if chg < 0 else ('#009900' if chg > 0 else '#333')
        sign  = '+' if chg > 0 else ''
        bg    = 'background:#F1F8E9' if idx % 2 == 0 else 'background:#FFFFFF'

        # K 值欄：顯示今/昨，今>昨用綠色
        k_t = _fmt(r.get('K值(9)'), '.1f')
        k_y = _fmt(r.get('K值昨'),  '.1f')
        k_html = (f"<b style='color:#27AE60'>{k_t}</b>"
                  f"<br><small style='color:#888'>昨{k_y}</small>")

        # OSC 欄：今/昨/前
        osc_t  = _fmt(r.get('OSC今'), '.4f')
        osc_y  = _fmt(r.get('OSC昨'), '.4f')
        osc_yy = _fmt(r.get('OSC前'), '.4f')
        osc_html = (f"<b style='color:#27AE60'>{osc_t}</b>"
                    f"<br><small style='color:#888'>昨{osc_y}</small>"
                    f"<br><small style='color:#aaa'>前{osc_yy}</small>")

        rows_html += f"""
<tr style='{bg}'>
  <td style='padding:5px 8px;font-weight:bold;color:#145A32'>{r.get('股票代號','')}</td>
  <td style='padding:5px 8px'>{r.get('股票名稱','')}</td>
  <td style='padding:5px 8px;text-align:right'>{_fmt(r.get('收盤價'), '.2f')}</td>
  <td style='padding:5px 8px;text-align:right;color:{chg_c}'>{sign}{chg:.2f}%</td>
  <td style='padding:5px 8px;text-align:right'>{_fmt(r.get('振幅(%)'), '.2f')}%</td>
  <td style='padding:5px 8px;text-align:right'>{int(r.get("量(張)", 0)):,}</td>
  <td style='padding:5px 8px;text-align:right'>{_fmt(r.get('換手率(%)'), '.2f')}%</td>
  <td style='padding:5px 8px;text-align:right'>{_fmt(r.get('P/E'), '.1f')}</td>
  <td style='padding:5px 8px;text-align:center'>{k_html}</td>
  <td style='padding:5px 8px;text-align:center;font-size:11px'>{osc_html}</td>
  <td style='padding:5px 8px;text-align:center;color:#27AE60;font-weight:bold'>
    {r.get('3日新高', '')}</td>
</tr>"""

    if not rows_html:
        rows_html = ("<tr><td colspan='12' style='padding:20px;text-align:center;"
                     "color:#888'>今日無符合條件股票</td></tr>",
                    ).replace("colspan='12'", "colspan='10'")

    return f"""<html><body style='font-family:Arial,sans-serif;font-size:13px'>
<h3>🎯 十全奧義 選股結果</h3>
<p>執行時間：{run_time.strftime('%Y-%m-%d %H:%M')} ／ 符合：<b>{n} 檔</b></p>
{logic_html}
<table border='1' cellspacing='0' cellpadding='0'
       style='border-collapse:collapse;font-size:12px;min-width:820px'>
  <thead>{header}</thead>
  <tbody>{rows_html}</tbody>
</table>
<p style='color:#aaa;font-size:10px;margin-top:16px'>
  資料來源：TWSE / TPEx Open API + yfinance ｜ 自動產生，僅供參考，投資風險自負
</p>
</body></html>"""


def send(csv_path: str):
    df       = pd.read_csv(csv_path, encoding='utf-8-sig')
    run_time = datetime.now()
    date_str = run_time.strftime('%Y/%m/%d')
    n        = len(df)

    subject = f"【十全奧義】{date_str} {n} 檔（3日新高＋KD上升＋MACD OSC V底）"
    body    = build_html(df, run_time)

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From']    = SENDER
    msg['To']      = RECEIVER
    msg.attach(MIMEText(body, 'html', 'utf-8'))

    # 附加 CSV 原始資料
    with open(csv_path, 'rb') as f:
        att = MIMEBase('application', 'octet-stream')
        att.set_payload(f.read())
    encoders.encode_base64(att)
    att.add_header('Content-Disposition', 'attachment',
                   filename=os.path.basename(csv_path))
    msg.attach(att)

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
        s.login(SENDER, APP_PASS)
        for addr in [a.strip() for a in RECEIVER.split(',')]:
            s.sendmail(SENDER, addr, msg.as_bytes())
    print(f'✅ 十全奧義 Email 寄出 → {RECEIVER}  主旨: {subject}')


if __name__ == '__main__':
    if len(sys.argv) > 1:
        csv_path = sys.argv[1]
    else:
        files = sorted(glob.glob('output/quan_*.csv'))
        if not files:
            print('找不到 quan_*.csv'); sys.exit(1)
        csv_path = files[-1]
    send(csv_path)
