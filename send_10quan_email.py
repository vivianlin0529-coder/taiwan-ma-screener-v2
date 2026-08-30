#!/usr/bin/env python3
"""
send_10quan_email.py — 十全奧義選股結果 Email
欄位: 代號|名稱|收盤|漲跌幅|振幅|量(張)|換手率|EPS(季)|P/E|K值|MACD OSC|今>昨>前
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


def build_html(df: pd.DataFrame, run_time: datetime) -> str:
    date_str = run_time.strftime('%Y/%m/%d')
    n = len(df)

    logic_html = """
<div style='background:#f0fff4;padding:10px;border-left:4px solid #27AE60;
            margin-bottom:12px;font-size:12px'>
<b>十全奧義 選股條件：</b><br>
<b>基本：</b>
① 股價創 3 日新高 &nbsp; ② 股價 &gt; 3 元<br>
<b>參數：</b>
P1 振幅 &gt;1% &nbsp;|&nbsp; P2 量 &gt;200張 &nbsp;|&nbsp; P3 換手率 &gt;0.5% &nbsp;|&nbsp;
P4 近季EPS &gt;0 &nbsp;|&nbsp; P5 PE &lt;20<br>
P8 K值今&gt;昨 &nbsp;|&nbsp; P9 MACD OSC今&gt;昨 &nbsp;|&nbsp;
P10 昨OSC&lt;前OSC（OSC V底反轉）<br>
<span style='color:#888;font-size:11px'>排序：成交量由大到小 ／ P6、P7 待確認後補入</span>
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
  <th style='padding:6px 8px'>EPS(季)</th>
  <th style='padding:6px 8px'>P/E</th>
  <th style='padding:6px 8px'>K值</th>
  <th style='padding:6px 8px'>MACD OSC<br><small>今/昨/前</small></th>
  <th style='padding:6px 8px'>3日<br>新高</th>
</tr>"""

    rows_html = ''
    for idx, r in df.iterrows():
        chg   = float(r.get('漲跌幅(%)', 0) or 0)
        chg_c = '#cc0000' if chg < 0 else ('#009900' if chg > 0 else '#333')
        sign  = '+' if chg > 0 else ''
        bg    = 'background:#E8F5E9' if idx % 2 == 0 else ''

        eps_s = f"{float(r['EPS(季)']):.2f}" if r.get('EPS(季)') not in (None, '', 'nan') else '─'
        pe_s  = f"{float(r['P/E']):.1f}"    if r.get('P/E')    not in (None, '', 'nan') else '─'

        osc_t  = r.get('MACD OSC', 0)
        osc_y  = r.get('OSC昨', 0)
        osc_yy = r.get('OSC前', 0)
        try:
            osc_html = (
                f"<b style='color:green'>{float(osc_t):.4f}</b><br>"
                f"<small style='color:#888'>昨{float(osc_y):.4f} 前{float(osc_yy):.4f}</small>"
            )
        except:
            osc_html = str(osc_t)

        rows_html += f"""
<tr style='{bg}'>
  <td style='padding:5px 8px;font-weight:bold;color:#145A32'>{r.get('股票代號','')}</td>
  <td style='padding:5px 8px'>{r.get('股票名稱','')}</td>
  <td style='padding:5px 8px;text-align:right'>{r.get('收盤價','')}</td>
  <td style='padding:5px 8px;text-align:right;color:{chg_c}'>{sign}{chg:.2f}%</td>
  <td style='padding:5px 8px;text-align:right'>{r.get('振幅(%)','')}%</td>
  <td style='padding:5px 8px;text-align:right'>{int(r.get("量(張)",0)):,}</td>
  <td style='padding:5px 8px;text-align:right'>{r.get('換手率(%)','0')}%</td>
  <td style='padding:5px 8px;text-align:right'>{eps_s}</td>
  <td style='padding:5px 8px;text-align:right'>{pe_s}</td>
  <td style='padding:5px 8px;text-align:right'>{r.get('K值','')}</td>
  <td style='padding:5px 8px;text-align:center'>{osc_html}</td>
  <td style='padding:5px 8px;text-align:center;color:#27AE60;font-weight:bold'>
    {r.get('3日新高','')}</td>
</tr>"""

    if not rows_html:
        rows_html = ("<tr><td colspan='12' style='padding:20px;text-align:center;color:#888'>"
                     "今日無符合條件股票</td></tr>")

    return f"""<html><body style='font-family:Arial,sans-serif;font-size:13px'>
<h3>🎯 十全奧義 選股結果</h3>
<p>執行時間：{run_time.strftime('%Y-%m-%d %H:%M')} ／ 符合：<b>{n} 檔</b></p>
{logic_html}
<table border='1' cellspacing='0' cellpadding='0'
       style='border-collapse:collapse;font-size:12px;min-width:780px'>
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

    subject  = f"【十全奧義】{date_str} {n} 檔（3日新高+KD+MACD V底）"
    body     = build_html(df, run_time)

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From']    = SENDER
    msg['To']      = RECEIVER
    msg.attach(MIMEText(body, 'html', 'utf-8'))

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
    print(f'✅ 十全奧義 Email 已寄出 → {RECEIVER}  主旨: {subject}')


if __name__ == '__main__':
    if len(sys.argv) > 1:
        csv_path = sys.argv[1]
    else:
        files = sorted(glob.glob('output/quan_*.csv'))
        if not files:
            print('找不到 quan_*.csv'); sys.exit(1)
        csv_path = files[-1]
    send(csv_path)
