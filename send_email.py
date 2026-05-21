#!/usr/bin/env python3
"""寄送篩選結果到指定 Email（Gmail SMTP + App Password）"""
import smtplib, sys, os, glob, ast
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import pandas as pd
from datetime import datetime

SENDER    = os.environ.get('GMAIL_SENDER', 'vivianlin0529@gmail.com')
RECIPIENT = 'vivianlin0529@gmail.com'
APP_PASS  = os.environ['GMAIL_APP_PASSWORD']


def _fmt_foreign(raw) -> str:
    """把 "[1200.0, 3400.0, 800.0]" 轉成可讀格式 "+1,200 / +3,400 / +800 張" """
    if pd.isna(raw) or str(raw).strip() in ('', '[]', 'nan'):
        return '—'
    try:
        nums = ast.literal_eval(str(raw))
        parts = []
        for n in nums:
            n = int(n)
            parts.append(f'+{n:,}' if n >= 0 else f'{n:,}')
        return ' / '.join(parts) + ' 張'
    except Exception:
        return str(raw)


def build_html_table(df: pd.DataFrame) -> str:
    cols = ['code', 'name', 'group', 'data_date', 'close', 'mktcap_yi',
            'vol_k', 'bias1', 'bias2', 'bias3', 'daily_spread', 'foreign_net_3d']
    # 相容舊版 CSV（沒有 foreign_net_3d 欄）
    for c in cols:
        if c not in df.columns:
            df[c] = '—'
    df = df[cols].copy()

    rows_html = ''
    for _, r in df.iterrows():
        mc = f"{r['mktcap_yi']:.0f}" if pd.notna(r['mktcap_yi']) and r['mktcap_yi'] != '—' else 'N/A'
        foreign_str = _fmt_foreign(r['foreign_net_3d'])
        rows_html += f"""
        <tr>
          <td>{r['code']}</td>
          <td>{r['name']}</td>
          <td>{r['group']}</td>
          <td>{r['data_date']}</td>
          <td style="text-align:right">{r['close']}</td>
          <td style="text-align:right">{mc}</td>
          <td style="text-align:right">{int(r['vol_k']):,}</td>
          <td style="text-align:right">{r['bias1']:.2f}</td>
          <td style="text-align:right">{r['bias2']:.2f}</td>
          <td style="text-align:right">{r['bias3']:.2f}</td>
          <td style="text-align:right;color:#c00"><b>{r['daily_spread']:.2f}</b></td>
          <td style="text-align:center;color:#006400;font-size:12px">{foreign_str}</td>
        </tr>"""

    return f"""
    <html><body>
    <h2 style="color:#333">📊 Taiwan MA Screener v2 — {datetime.now().strftime('%Y-%m-%d')}</h2>
    <p>篩選條件：排除ETF/金融/紡織/電信 ｜ 量≥1000張 ｜ BIAS三線糾結 ｜ 日線縮軌 ｜ 
       <b style="color:#006400">外資連續買超 3 日</b></p>
    <p>共 <b>{len(df)}</b> 檔通過，依成交量排序：</p>
    <table border="1" cellpadding="5" cellspacing="0"
           style="border-collapse:collapse;font-size:13px;font-family:monospace">
      <tr style="background:#2c3e50;color:white">
        <th>代號</th><th>名稱</th><th>產業</th><th>資料日</th>
        <th>收盤</th><th>市值(億)</th><th>量(張)</th>
        <th>BIAS1%</th><th>BIAS2%</th><th>BIAS3%</th><th>日縮%</th>
        <th>外資連買(d-2/d-1/d0)</th>
      </tr>
      {rows_html}
    </table>
    <br>
    <p style="color:#555;font-size:12px">
      ＊外資連買欄：顯示最近三日外資/陸資淨買超股數（張），三日皆需為正值方才入選。
    </p>
    <p style="color:#999;font-size:11px">
      資料來源：TWSE / TPEx Open API + yfinance ｜ 自動產生，請自行確認後再操作
    </p>
    </body></html>"""


def send(csv_path: str):
    df = pd.read_csv(csv_path, encoding='utf-8-sig')
    date_str = datetime.now().strftime('%Y-%m-%d')
    msg = MIMEMultipart('alternative')
    msg['Subject'] = f'📊 台股均線縮軌篩選 {date_str}｜{len(df)} 檔通過｜外資連買3日'
    msg['From']    = SENDER
    msg['To']      = RECIPIENT
    msg.attach(MIMEText(build_html_table(df), 'html', 'utf-8'))
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
        files = sorted(glob.glob('output/screener_*.csv'))
        if not files:
            print('找不到 CSV'); sys.exit(1)
        csv_path = files[-1]
    send(csv_path)
