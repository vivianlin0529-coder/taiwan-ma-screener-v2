#!/usr/bin/env python3
"""
send_email.py — 台股均線糾結篩選結果 Email 發送
列欄: 代號|名稱|市值|收盤|漲跌幅|MA5|MA10|MA22|方向5/10/22|BIAS2|布林上軌(今/昨)|布林下軌(今/昨)|布林狀態|達華斯箱|量(張)
底色: 橘=外資連三買超 粉=營收超越去年 黃=達華斯箱 紅=突破上軌 橙=跌破下軌
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


def _row_bg(r):
    """決定行底色（優先順序：外資橘 > 營收粉 > 箱型黃 > 布林紅/橙）"""
    if str(r.get('外資連三買', '')).lower() in ('true','1','yes'):
        return 'background:#FFE0B2'     # 橘底 — 外資連三買超
    if str(r.get('營收超越去年', '')).lower() in ('true','1','yes'):
        return 'background:#FCE4EC'     # 粉紅底 — 營收超越去年同期
    if str(r.get('達華斯箱', '')) == '✓':
        return 'background:#FFFDE7'     # 黃底 — 達華斯箱型
    bb = str(r.get('布林狀態', ''))
    if bb == '突破上軌':
        return 'background:#FFEBEE'     # 淡紅
    if bb == '跌破下軌':
        return 'background:#FFF3E0'     # 淡橙
    return ''


def build_html(df: pd.DataFrame, run_time: datetime) -> str:
    date_str = run_time.strftime('%Y/%m/%d')
    n = len(df)

    # ── 篩選說明欄 ──
    logic_html = """
<div style='background:#f0f4ff;padding:10px;border-left:4px solid #4472C4;margin-bottom:12px;font-size:12px'>
<b>篩選邏輯（由大到小）：</b><br>
① 排除 ETF &nbsp;② 排除金融保險股 &nbsp;③ 成交量 ≥ 1000 張<br>
④ 日線方向：排除 MA5↓MA10↓MA22↓（下下下）；週線需開始往上<br>
⑤ 日線均線糾結（三線合一）：BIAS1&lt;2.4% | BIAS2&lt;3.0% | BIAS3&lt;6.4%<br>
⑥ 布林縮軌：今日上軌 &lt; 昨日上軌 <b>且</b> 今日下軌 &gt; 昨日下軌<br>
排序：成交量由大到小
</div>"""

    # ── 圖例 ──
    legend = """
<p style='font-size:11px;color:#555;margin:4px 0'>
🟠 橘底=外資連三買超 &nbsp;|&nbsp;
🩷 粉底=營收超越去年同期 &nbsp;|&nbsp;
🟡 黃底=達華斯箱型 &nbsp;|&nbsp;
🔴 淡紅=突破上軌 &nbsp;|&nbsp;
🟤 淡橙=跌破下軌
</p>"""

    # ── 表頭 ──
    header = """
<tr style='background:#4472C4;color:white;text-align:center;font-size:12px'>
  <th style='padding:6px 8px'>代號</th>
  <th style='padding:6px 8px'>名稱</th>
  <th style='padding:6px 8px'>市值(億)</th>
  <th style='padding:6px 8px'>收盤</th>
  <th style='padding:6px 8px'>漲跌幅</th>
  <th style='padding:6px 8px'>MA5</th>
  <th style='padding:6px 8px'>MA10</th>
  <th style='padding:6px 8px'>MA22</th>
  <th style='padding:6px 8px'>方向<br><small>5/10/22</small></th>
  <th style='padding:6px 8px'>BIAS2</th>
  <th style='padding:6px 8px'>布林上軌<br><small>今/昨</small></th>
  <th style='padding:6px 8px'>布林下軌<br><small>今/昨</small></th>
  <th style='padding:6px 8px'>布林狀態</th>
  <th style='padding:6px 8px'>達華斯箱</th>
  <th style='padding:6px 8px'>量(張)</th>
</tr>"""

    rows_html = ''
    for _, r in df.iterrows():
        bg       = _row_bg(r)
        chg      = float(r.get('漲跌幅(%)', 0) or 0)
        chg_c    = '#cc0000' if chg < 0 else ('#009900' if chg > 0 else '#333')
        sign     = '+' if chg > 0 else ''
        dirs     = f"{r.get('MA5方向','?')} {r.get('MA10方向','?')} {r.get('MA22方向','?')}"
        bb_stat  = str(r.get('布林狀態','')) or '─'
        dvs      = str(r.get('達華斯箱',''))
        dvs_html = f"<b style='color:#cc8800'>✓箱型</b>" if dvs == '✓' else '─'
        mc       = r.get('市值(億)')
        mc_str   = f"{float(mc):,.0f} 億" if mc and str(mc) not in ('','nan','None') else '─'

        bias2_val = r.get('BIAS2', 0)
        try:    bias2_pct = f"{float(bias2_val):.4%}"
        except: bias2_pct = str(bias2_val)

        bbu_t = r.get('布林上軌',  '─'); bbu_y = r.get('布林上軌昨', '─')
        bbl_t = r.get('布林下軌',  '─'); bbl_y = r.get('布林下軌昨', '─')
        try: bbu_str = f"{float(bbu_t):.2f}<br><small style='color:#888'>昨{float(bbu_y):.2f}</small>"
        except: bbu_str = str(bbu_t)
        try: bbl_str = f"{float(bbl_t):.2f}<br><small style='color:#888'>昨{float(bbl_y):.2f}</small>"
        except: bbl_str = str(bbl_t)

        bb_html = f"<b style='color:red'>{bb_stat}</b>" if bb_stat not in ('─','') else '─'

        rows_html += f"""
<tr style='{bg}'>
  <td style='padding:5px 8px;font-weight:bold;color:#1a237e'>{r.get('股票代號','')}</td>
  <td style='padding:5px 8px'>{r.get('股票名稱','')}</td>
  <td style='padding:5px 8px;text-align:right'>{mc_str}</td>
  <td style='padding:5px 8px;text-align:right'>{r.get('收盤價','')}</td>
  <td style='padding:5px 8px;text-align:right;color:{chg_c}'>{sign}{chg:.2f}%</td>
  <td style='padding:5px 8px;text-align:center'>{r.get('MA5','')}</td>
  <td style='padding:5px 8px;text-align:center'>{r.get('MA10','')}</td>
  <td style='padding:5px 8px;text-align:center'>{r.get('MA22','')}</td>
  <td style='padding:5px 8px;text-align:center;font-size:16px'>{dirs}</td>
  <td style='padding:5px 8px;text-align:right'>{bias2_pct}</td>
  <td style='padding:5px 8px;text-align:right'>{bbu_str}</td>
  <td style='padding:5px 8px;text-align:right'>{bbl_str}</td>
  <td style='padding:5px 8px;text-align:center'>{bb_html}</td>
  <td style='padding:5px 8px;text-align:center'>{dvs_html}</td>
  <td style='padding:5px 8px;text-align:right'>{int(r.get("成交量(張)",0)):,}</td>
</tr>"""

    if not rows_html:
        rows_html = "<tr><td colspan='15' style='padding:20px;text-align:center;color:#888'>今日無符合條件股票</td></tr>"

    return f"""<html><body style='font-family:Arial,sans-serif;font-size:13px'>
<h3>📊 台股均線糾結（三線合一）篩選結果</h3>
<p>執行時間：{run_time.strftime('%Y-%m-%d %H:%M')} ／ 符合：<b>{n} 檔</b></p>
{logic_html}
<table border='1' cellspacing='0' cellpadding='0'
       style='border-collapse:collapse;font-size:12px;min-width:800px'>
  <thead>{header}</thead>
  <tbody>{rows_html}</tbody>
</table>
{legend}
<p style='color:#aaa;font-size:10px;margin-top:16px'>
  資料來源：TWSE / TPEx Open API + yfinance + MOPS ｜ 自動產生，僅供參考，投資風險自負
</p>
</body></html>"""


def send(csv_path: str):
    df = pd.read_csv(csv_path, encoding='utf-8-sig')
    run_time = datetime.now()
    date_str = run_time.strftime('%Y/%m/%d')
    n = len(df)

    subject = f"【台股均線糾結】{date_str} {n} 檔（BIAS+縮軌｜排除ETF金融）"
    body_html = build_html(df, run_time)

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From']    = SENDER
    msg['To']      = RECEIVER
    msg.attach(MIMEText(body_html, 'html', 'utf-8'))

    # 附上 CSV
    with open(csv_path, 'rb') as f:
        att = MIMEBase('application', 'octet-stream')
        att.set_payload(f.read())
    encoders.encode_base64(att)
    att.add_header('Content-Disposition', 'attachment', filename=os.path.basename(csv_path))
    msg.attach(att)

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
        s.login(SENDER, APP_PASS)
        for addr in [a.strip() for a in RECEIVER.split(',')]:
            s.sendmail(SENDER, addr, msg.as_bytes())
    print(f'✅ Email 已寄出 → {RECEIVER}  主旨: {subject}')


if __name__ == '__main__':
    if len(sys.argv) > 1:
        csv_path = sys.argv[1]
    else:
        files = sorted(glob.glob('output/screener_*.csv'))
        if not files:
            print('找不到 CSV'); sys.exit(1)
        csv_path = files[-1]
    send(csv_path)
