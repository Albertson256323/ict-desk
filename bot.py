import time
import json
import csv
import os
import traceback

import ccxt
import pandas as pd

STATE_FILE = 'state.json'
JOURNAL_FILE = 'trading_journal.csv'
DASHBOARD_FILE = 'docs/index.html'

exchange = ccxt.okx({
    'options': {'defaultType': 'swap'},
    'enableRateLimit': True,
})

symbols = [
    'BTC/USDT:USDT', 'ETH/USDT:USDT', 'SOL/USDT:USDT', 'BNB/USDT:USDT', 'XRP/USDT:USDT',
    'ADA/USDT:USDT', 'LINK/USDT:USDT', 'DOGE/USDT:USDT', 'AVAX/USDT:USDT', 'NEAR/USDT:USDT'
]

JOURNAL_HEADERS = ["Timestamp", "Symbol", "Type", "Entry", "StopLoss", "TakeProfit", "Outcome", "Notes"]


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}


def save_state(active_trades):
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(active_trades, f, indent=2)


def ensure_journal():
    if not os.path.exists(JOURNAL_FILE):
        with open(JOURNAL_FILE, 'w', encoding='utf-8', newline='') as f:
            csv.writer(f).writerow(JOURNAL_HEADERS)


def log_to_journal(timestamp, symbol, trade_type, entry, sl, tp, outcome, notes):
    ensure_journal()
    with open(JOURNAL_FILE, 'a', encoding='utf-8', newline='') as f:
        csv.writer(f).writerow([timestamp, symbol, trade_type, entry, sl, tp, outcome, notes])


def read_journal():
    ensure_journal()
    rows = []
    with open(JOURNAL_FILE, 'r', encoding='utf-8', newline='') as f:
        for row in csv.DictReader(f):
            rows.append(row)
    rows.reverse()
    return rows


def run_scan_cycle():
    active_trades = load_state()
    current_time = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime()) + ' UTC'

    for symbol in symbols:
        if symbol in active_trades:
            continue
        try:
            h4_candles = exchange.fetch_ohlcv(symbol, timeframe='4h', limit=30)
            df_h4 = pd.DataFrame(h4_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

            m15_candles = exchange.fetch_ohlcv(symbol, timeframe='15m', limit=30)
            df_15 = pd.DataFrame(m15_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

            if len(df_15) < 11 or len(df_h4) < 2:
                continue

            df_15['body'] = abs(df_15['close'] - df_15['open'])
            avg_body = df_15['body'].rolling(window=10).mean().iloc[-1]
            latest_body = df_15.iloc[-1]['body']
            is_displacement = latest_body > (avg_body * 2.2)

            c1_high = df_15.iloc[-3]['high']
            c3_low = df_15.iloc[-1]['low']
            has_fvg = c3_low > c1_high

            h4_bullish = df_h4.iloc[-1]['close'] > df_h4.iloc[-1]['open']

            if is_displacement and has_fvg and h4_bullish:
                fvg_entry = (c3_low + c1_high) / 2
                stop_loss = df_15.iloc[-5:-1]['low'].min()
                risk_distance = fvg_entry - stop_loss

                if risk_distance <= 0:
                    continue

                take_profit = fvg_entry + (risk_distance * 6)

                active_trades[symbol] = {
                    'type': 'LONG',
                    'entry': fvg_entry,
                    'stop_loss': stop_loss,
                    'take_profit': take_profit,
                    'last_price': fvg_entry,
                    'last_check': current_time,
                }
                rationale = "15m structural displacement with verified FVG, confirmed by bullish H4 context."
                log_to_journal(current_time, symbol, 'LONG', fvg_entry, stop_loss, take_profit, 'OPEN', rationale)

        except Exception:
            print(f"Error scanning {symbol}:")
            traceback.print_exc()

    for symbol, trade in list(active_trades.items()):
        try:
            ticker = exchange.fetch_ticker(symbol)
            current_price = ticker['last']
            trade['last_price'] = current_price
            trade['last_check'] = current_time

            if current_price >= trade['take_profit']:
                log_to_journal(current_time, symbol, trade['type'], trade['entry'], trade['stop_loss'], trade['take_profit'], 'WIN', 'Target reached.')
                del active_trades[symbol]
            elif current_price <= trade['stop_loss']:
                log_to_journal(current_time, symbol, trade['type'], trade['entry'], trade['stop_loss'], trade['take_profit'], 'LOSS', 'Stop loss hit.')
                del active_trades[symbol]

        except Exception:
            print(f"Error monitoring {symbol}:")
            traceback.print_exc()

    save_state(active_trades)
    return active_trades, current_time


def fmt(n, decimals=4):
    try:
        return f"{n:,.{decimals}f}"
    except (TypeError, ValueError):
        return str(n)


def clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, v))


def ladder_position(value, sl, tp):
    span = tp - sl
    if span == 0:
        return 0.0
    return clamp(((value - sl) / span) * 100.0)


def build_trade_card(symbol, trade):
    entry, sl, tp = trade['entry'], trade['stop_loss'], trade['take_profit']
    last_price = trade.get('last_price', entry)
    last_check = trade.get('last_check', '—')

    entry_pos = ladder_position(entry, sl, tp)
    price_pos = ladder_position(last_price, sl, tp)
    risk = entry - sl
    reward = tp - entry
    rr = (reward / risk) if risk else 0
    ticker = symbol.split('/')[0]

    return f"""
    <div class="trade-card">
        <div class="trade-card-head">
            <span class="ticker">{ticker}</span>
            <span class="dir-tag dir-{trade['type'].lower()}">{trade['type']}</span>
            <span class="rr-tag">R:R 1:{rr:.1f}</span>
        </div>
        <div class="ladder">
            <div class="ladder-track"></div>
            <div class="ladder-marker entry" style="left:{entry_pos:.2f}%" title="Entry {fmt(entry)}"></div>
            <div class="ladder-marker price" style="left:{price_pos:.2f}%" title="Last checked {fmt(last_price)}"></div>
        </div>
        <div class="ladder-labels">
            <span class="lbl-sl">SL {fmt(sl)}</span>
            <span class="lbl-entry">Entry {fmt(entry)}</span>
            <span class="lbl-tp">TP {fmt(tp)}</span>
        </div>
        <div class="trade-card-foot">Last checked {last_check}</div>
    </div>
    """


def build_journal_rows(logs_snapshot):
    if not logs_snapshot:
        return '<tr><td colspan="6" class="empty-cell">No trades logged yet.</td></tr>'

    rows = ""
    for log in logs_snapshot:
        outcome_class = {"WIN": "pill-win", "LOSS": "pill-loss"}.get(log['Outcome'], "pill-open")
        rows += f"""
        <tr>
            <td class="mono">{log['Timestamp']}</td>
            <td class="ticker-cell">{log['Symbol'].split('/')[0]}</td>
            <td>{log['Type']}</td>
            <td class="mono">{fmt(float(log['Entry']))}</td>
            <td><span class="pill {outcome_class}">{log['Outcome']}</span></td>
            <td class="rationale">{log['Notes']}</td>
        </tr>
        """
    return rows


def build_stat_tiles(open_count, closed_count, wins, last_scan):
    win_rate = (wins / closed_count * 100) if closed_count else 0.0
    tiles = [
        ("LAST SCAN", last_scan),
        ("OPEN POSITIONS", str(open_count)),
        ("CLOSED TRADES", str(closed_count)),
        ("WIN RATE", f"{win_rate:.1f}%"),
    ]
    html = ""
    for label, value in tiles:
        html += f"""
        <div class="stat-tile">
            <span class="stat-value">{value}</span>
            <span class="stat-label">{label}</span>
        </div>
        """
    return html


PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Displacement Desk — ICT Engine</title>
<meta http-equiv="refresh" content="120">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
    :root {{
        --bg: #0B0D10; --surface: #14171B; --surface-alt: #1B1F24; --line: #262B31;
        --text: #E7E5E0; --text-muted: #8A8F98; --accent: #D4A24C;
        --long: #4F9C7C; --short: #C1554A; --loss: #C1554A;
    }}
    * {{ box-sizing: border-box; }}
    @media (prefers-reduced-motion: reduce) {{ * {{ animation: none !important; transition: none !important; }} }}
    body {{
        margin: 0; background: var(--bg); color: var(--text);
        font-family: 'IBM Plex Sans', 'Inter', system-ui, sans-serif;
        padding: 18px; max-width: 960px; margin-inline: auto;
    }}
    .mono, .stat-value, .ladder-labels span, td.mono {{
        font-family: 'IBM Plex Mono', 'Menlo', monospace; font-variant-numeric: tabular-nums;
    }}
    header.masthead {{
        display: flex; justify-content: space-between; align-items: flex-end;
        border-bottom: 1px solid var(--line); padding-bottom: 14px; margin-bottom: 18px;
    }}
    .masthead-title {{ font-weight: 600; font-size: 22px; letter-spacing: 0.02em; margin: 0; }}
    .masthead-sub {{ color: var(--text-muted); font-size: 12px; letter-spacing: 0.04em; text-transform: uppercase; margin-top: 4px; }}
    .status-pill {{ display: inline-flex; align-items: center; gap: 7px; font-size: 12px; letter-spacing: 0.05em; color: var(--long); text-transform: uppercase; }}
    .pulse-dot {{ width: 7px; height: 7px; border-radius: 50%; background: var(--long); animation: pulse 2s infinite; }}
    @keyframes pulse {{ 0% {{ box-shadow: 0 0 0 0 rgba(79,156,124,0.55); }} 70% {{ box-shadow: 0 0 0 6px rgba(79,156,124,0); }} 100% {{ box-shadow: 0 0 0 0 rgba(79,156,124,0); }} }}
    .stat-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 1px; background: var(--line); border: 1px solid var(--line); border-radius: 6px; overflow: hidden; margin-bottom: 20px; }}
    .stat-tile {{ background: var(--surface); padding: 14px 16px; display: flex; flex-direction: column; gap: 4px; }}
    .stat-value {{ font-size: 18px; font-weight: 600; }}
    .stat-label {{ font-size: 10.5px; color: var(--text-muted); letter-spacing: 0.08em; text-transform: uppercase; }}
    section.panel {{ background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 18px; margin-bottom: 18px; }}
    .panel-title {{ display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 14px; }}
    .panel-title h2 {{ font-size: 13px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--text-muted); margin: 0; font-weight: 600; }}
    .panel-title .count {{ font-size: 12px; color: var(--text-muted); }}
    .trade-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; }}
    .trade-card {{ background: var(--surface-alt); border: 1px solid var(--line); border-radius: 6px; padding: 14px; }}
    .trade-card-head {{ display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }}
    .ticker {{ font-weight: 600; font-size: 14px; }}
    .dir-tag {{ font-size: 10px; letter-spacing: 0.06em; padding: 2px 6px; border-radius: 3px; font-weight: 600; }}
    .dir-long {{ background: rgba(79,156,124,0.15); color: var(--long); }}
    .dir-short {{ background: rgba(193,85,74,0.15); color: var(--short); }}
    .rr-tag {{ margin-left: auto; font-size: 11px; color: var(--text-muted); }}
    .ladder {{ position: relative; height: 6px; margin: 10px 2px 6px; }}
    .ladder-track {{ position: absolute; top: 50%; left: 0; right: 0; height: 3px; transform: translateY(-50%); background: linear-gradient(90deg, var(--short), var(--line) 45%, var(--line) 55%, var(--long)); border-radius: 2px; opacity: 0.55; }}
    .ladder-marker {{ position: absolute; top: 50%; width: 9px; height: 9px; border-radius: 50%; transform: translate(-50%, -50%); }}
    .ladder-marker.entry {{ background: var(--text); border: 2px solid var(--surface-alt); }}
    .ladder-marker.price {{ background: var(--accent); box-shadow: 0 0 0 3px rgba(212,162,76,0.25); }}
    .ladder-labels {{ display: flex; justify-content: space-between; font-size: 10.5px; color: var(--text-muted); }}
    .ladder-labels .lbl-entry {{ color: var(--text); }}
    .trade-card-foot {{ margin-top: 10px; font-size: 10.5px; color: var(--text-muted); }}
    .empty-state {{ color: var(--text-muted); font-size: 13px; padding: 10px 2px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th {{ text-align: left; font-size: 10.5px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--text-muted); font-weight: 600; padding: 8px 10px; border-bottom: 1px solid var(--line); }}
    td {{ padding: 9px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }}
    tr:last-child td {{ border-bottom: none; }}
    .ticker-cell {{ font-weight: 600; }}
    .rationale {{ color: var(--text-muted); }}
    .empty-cell {{ color: var(--text-muted); text-align: center; padding: 20px; }}
    .pill {{ font-size: 10.5px; letter-spacing: 0.05em; padding: 2px 8px; border-radius: 20px; font-weight: 600; }}
    .pill-win {{ background: rgba(79,156,124,0.15); color: var(--long); }}
    .pill-loss {{ background: rgba(193,85,74,0.15); color: var(--loss); }}
    .pill-open {{ background: rgba(212,162,76,0.15); color: var(--accent); }}
    footer {{ color: var(--text-muted); font-size: 11px; text-align: center; padding-top: 8px; }}
</style>
</head>
<body>
    <header class="masthead">
        <div>
            <p class="masthead-title">Displacement Desk</p>
            <p class="masthead-sub">15m structural displacement · FVG · H4 confirmation</p>
        </div>
        <span class="status-pill"><span class="pulse-dot"></span>Scheduled scan</span>
    </header>

    <div class="stat-grid">{stat_tiles}</div>

    <section class="panel">
        <div class="panel-title"><h2>Active Setups</h2><span class="count">{open_count} open</span></div>
        {trades_html}
    </section>

    <section class="panel">
        <div class="panel-title"><h2>Trade Journal</h2><span class="count">last {shown_count} of {total_count}</span></div>
        <table>
            <tr><th>Time</th><th>Asset</th><th>Type</th><th>Entry</th><th>Outcome</th><th>Rationale</th></tr>
            {journal_rows}
        </table>
    </section>

    <footer>Updated on each scheduled scan (roughly every 15–30 min) · paper-trading simulation, no live orders placed</footer>
</body>
</html>
"""


def render_dashboard(active_trades, last_scan):
    logs = read_journal()
    wins = sum(1 for log in logs if log['Outcome'] == 'WIN')
    closed_count = sum(1 for log in logs if log['Outcome'] in ('WIN', 'LOSS'))
    shown = logs[:15]

    if active_trades:
        trades_html = '<div class="trade-grid">' + "".join(
            build_trade_card(sym, trade) for sym, trade in active_trades.items()
        ) + '</div>'
    else:
        trades_html = '<p class="empty-state">No setups currently open. Next scheduled scan will check again.</p>'

    html = PAGE_TEMPLATE.format(
        stat_tiles=build_stat_tiles(len(active_trades), closed_count, wins, last_scan),
        open_count=len(active_trades),
        trades_html=trades_html,
        shown_count=len(shown),
        total_count=len(logs),
        journal_rows=build_journal_rows(shown),
    )

    os.makedirs(os.path.dirname(DASHBOARD_FILE), exist_ok=True)
    with open(DASHBOARD_FILE, 'w', encoding='utf-8') as f:
        f.write(html)


if __name__ == '__main__':
    trades, scan_time = run_scan_cycle()
    render_dashboard(trades, scan_time)
    print(f"Scan complete at {scan_time}. Open positions: {len(trades)}.")
