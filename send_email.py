#!/usr/bin/env python3
"""
寄送篩選結果到指定 Email（Gmail SMTP + App Password）
用法: python send_email.py output/screener_YYYYMMDD.csv
"""
import smtplib, sys, os, glob
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import pandas as pd
from datetime import datetime

SENDER    = os.environ.get('GMAIL_SENDER', 'vivianlin0529@gmail.com')
RECIPIENT = 'vivianlin0529@gmail.com'
APP_PASS  = os.environ['GMAIL_APP_PASSWORD']   # GitHub Secret


def build_html_table(df: pd.DataFrame) -> str:
    cols = ['code','name','group','data_date','close','mktcap_yi',
            'vol_k','bias1','bias2','bias3','daily_spread','weekly_spread']
    df = df[cols].copy()
    df.columns = ['代號','名稱','產業','資料日','收盤','市值(億)',
                  '量(張)','BIAS1%','BIAS2%','BIAS3%','日縮%','週縮%']

    rows_html = ''
    for _, r in df.iterrows():
        mc = f"{r['市值(億)']:.0f}" if pd.notna(r['市值(億)']) else 'N/A'
        rows_html += f"""
        <tr>
          <td>{r['代號']}</td><td>{r['名稱']}</td><td>{r['產業']}</td>
          <td>{r['資料日']}</td><td style="text-align:right">{r['收盤']}</td>
          <td style="text-align:right">{mc}</td>
          <td style="text-align:right">{int(r['量(張)']):,}</td>
          <td style="text-align:right">{r['BIAS1%']:.2f}</td>
          <td style="text-align:right">{r['BIAS2%']:.2f}</td>
          <td style="text-align:right">{r['BIAS3%']:.2f}</td>
          <td style="text-align:right;color:#c00"><b>{r['日縮%']:.2f}</b></td>
          <td style="text-align:right;color:#060"><b>{r['週縮%']:.2f}</b></td>
        </tr>"""

    return f"""
    <html><body>
    <h2 style="color:#333">📊 Taiwan MA Screener v2 — {datetime.now().strftime('%Y-%m-%d')}</h2>
    <p>篩選條件：排除ETF/金融/紡織/電信 ｜ 量≥1000張 ｜ BIAS三線糾結 ｜ 日線縮軌 ｜ 週線縮軌</p>
    <p>共 <b>{len(df)}</b> 檔通過，依日縮幅度排序：</p>
    <table border="1" cellpadding="5" cellspacing="0"
           style="border-collapse:collapse;font-size:13px;font-family:monospace">
      <tr style="background:#2c3e50;color:white">
        <th>代號</th><th>名稱</th><th>產業</th><th>資料日</th>
        <th>收盤</th><th>市值(億)</th><th>量(張)</th>
        <th>BIAS1%</th><th>BIAS2%</th><th>BIAS3%</th>
        <th>日縮%</th><th>週縮%</th>
      </tr>
      {rows_html}
    </table>
    <br><p style="color:#999;font-size:11px">
      資料來源：TWSE / TPEx Open API + yfinance ｜ 自動產生，請自行確認後再操作
    </p>
    </body></html>"""


def send(csv_path: str):
    df = pd.read_csv(csv_path, encoding='utf-8-sig')
    date_str = datetime.now().strftime('%Y-%m-%d')

    msg = MIMEMultipart('alternative')
    msg['Subject'] = f'📊 台股均線縮軌篩選 {date_str}｜{len(df)} 檔通過'
    msg['From']    = SENDER
    msg['To']      = RECIPIENT

    # HTML 內文
    msg.attach(MIMEText(build_html_table(df), 'html', 'utf-8'))

    # CSV 附件
    with open(csv_path, 'rb') as f:
        att = MIMEBase('application', 'octet-stream')
        att.set_payload(f.read())
    encoders.encode_base64(att)
    att.add_header('Content-Disposition', 'attachment',
                   filename=os.path.basename(csv_path))
    msg.attach(att)

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
        s.login(SENDER, APP_PASS)
        s.send_message(msg)
    print(f'✅ Email 已寄出 → {RECIPIENT}')


if __name__ == '__main__':
    if len(sys.argv) > 1:
        csv_path = sys.argv[1]
    else:
        # 找最新的 output csv
        files = sorted(glob.glob('output/screener_*.csv'))
        if not files:
            print('找不到 CSV 檔案')
            sys.exit(1)
        csv_path = files[-1]
    send(csv_path)
