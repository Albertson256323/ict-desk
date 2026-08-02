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


SCAN_INTERVAL_SECONDS = 15 * 60  # matches the cron schedule in scan.yml


def run_scan_cycle():
    active_trades = load_state()
    now_epoch = time.time()
    current_time = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(now_epoch)) + ' UTC'
    current_time_iso = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now_epoch))

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

                MIN_RR = 3.0
                swing_high = df_15['high'].iloc[:-1].max()
                reward_distance = swing_high - fvg_entry

                if reward_distance <= 0:
                    continue

                rr = reward_distance / risk_distance
                if rr < MIN_RR:
                    continue

                take_profit = swing_high

                active_trades[symbol] = {
                    'type': 'LONG',
                    'entry': fvg_entry,
                    'stop_loss': stop_loss,
                    'take_profit': take_profit,
                    'last_price': fvg_entry,
                    'last_check': current_time,
                }
                rationale = f"15m structural displacement with verified FVG, confirmed by bullish H4 context. Target: recent swing high, R:R 1:{rr:.1f}."
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
    return active_trades, current_time, current_time_iso


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

    ticks = "".join(
        f'<div class="ladder-tick" style="left:{p:.2f}%"></div>'
        for p in (100 * i / 6 for i in range(1, 6))
    )

    return f"""
    <div class="trade-card">
        <div class="trade-card-head">
            <span class="ticker">{ticker}</span>
            <span class="dir-tag dir-{trade['type'].lower()}">{trade['type']}</span>
            <span class="rr-tag">R:R 1:{rr:.1f}</span>
        </div>
        <div class="ladder">
            <div class="ladder-track"></div>
            {ticks}
            <div class="ladder-marker entry" style="left:{entry_pos:.2f}%" title="Entry {fmt(entry)}"></div>
            <div class="ladder-marker price" style="left:{price_pos:.2f}%" title="Last checked {fmt(last_price)}"></div>
        </div>
        <div class="ladder-labels">
            <span class="lbl-sl">SL<br>{fmt(sl)}</span>
            <span class="lbl-entry">ENTRY<br>{fmt(entry)}</span>
            <span class="lbl-tp">TP<br>{fmt(tp)}</span>
        </div>
        <div class="trade-card-foot">Last checked {last_check}</div>
    </div>
    """


def build_journal_rows(logs_snapshot):
    if not logs_snapshot:
        return '<tr><td colspan="6" class="empty-cell">No trades logged yet. The first one will appear here the moment a setup opens.</td></tr>'

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
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
    :root {{
        --ink: #12201C; --panel: #1A2B24; --panel-raised: #22362C; --rule: #2E4238;
        --chalk: #EDEAE0; --chalk-dim: #8FA79B;
        --marigold: #E0A458; --win: #7FB69E; --loss: #D9836F;
    }}
    * {{ box-sizing: border-box; }}
    @media (prefers-reduced-motion: reduce) {{ * {{ animation: none !important; transition: none !important; }} }}
    body {{
        margin: 0; background: var(--ink); color: var(--chalk);
        font-family: 'IBM Plex Sans', system-ui, sans-serif;
        padding: 20px 16px 32px; max-width: 720px; margin-inline: auto;
        -webkit-font-smoothing: antialiased;
    }}
    .mono, .stat-value, .ladder-labels span, td.mono {{
        font-family: 'IBM Plex Mono', 'Menlo', monospace; font-variant-numeric: tabular-nums;
    }}

    header.masthead {{ padding-bottom: 16px; margin-bottom: 22px; }}
    .masthead-row {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }}
    .masthead-title {{
        font-family: 'Space Grotesk', sans-serif; font-weight: 700; font-size: 25px;
        letter-spacing: -0.01em; margin: 0; color: var(--chalk);
    }}
    .masthead-rule {{ height: 3px; width: 46px; background: var(--marigold); border-radius: 2px; margin-top: 9px; }}
    .masthead-sub {{ color: var(--chalk-dim); font-size: 11.5px; letter-spacing: 0.05em; text-transform: uppercase; margin-top: 8px; }}
    .status-pill {{
        display: inline-flex; align-items: center; gap: 7px; font-size: 11px; letter-spacing: 0.08em;
        color: var(--win); text-transform: uppercase; white-space: nowrap; margin-top: 2px;
    }}
    .pulse-dot {{ width: 6px; height: 6px; border-radius: 50%; background: var(--win); animation: pulse 2s infinite; flex-shrink: 0; }}
    @keyframes pulse {{ 0% {{ box-shadow: 0 0 0 0 rgba(127,182,158,0.55); }} 70% {{ box-shadow: 0 0 0 6px rgba(127,182,158,0); }} 100% {{ box-shadow: 0 0 0 0 rgba(127,182,158,0); }} }}

    .stat-strip {{
        display: grid; grid-template-columns: repeat(auto-fit, minmax(126px, 1fr));
        background: var(--panel); border: 1px solid var(--rule); border-radius: 10px;
        margin-bottom: 22px; overflow: hidden;
        box-shadow: 0 1px 0 rgba(0,0,0,0.2) inset;
    }}
    .stat-tile {{
        padding: 15px 16px; display: flex; flex-direction: column; gap: 5px;
        border-right: 1px dashed var(--rule); border-bottom: 1px dashed var(--rule);
    }}
    .stat-tile:nth-child(3n) {{ border-right: none; }}
    .stat-value {{ font-size: 17px; font-weight: 600; letter-spacing: -0.01em; }}
    .stat-label {{ font-size: 10px; color: var(--chalk-dim); letter-spacing: 0.09em; text-transform: uppercase; }}

    section.panel {{
        background: var(--panel); border: 1px solid var(--rule); border-radius: 10px;
        padding: 18px 16px; margin-bottom: 18px;
    }}
    .panel-title {{ display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 14px; }}
    .panel-title h2 {{
        font-family: 'Space Grotesk', sans-serif; font-size: 13px; letter-spacing: 0.06em;
        text-transform: uppercase; color: var(--chalk-dim); margin: 0; font-weight: 600;
    }}
    .panel-title .count {{ font-size: 12px; color: var(--chalk-dim); }}

    .trade-grid {{ display: grid; gap: 12px; }}
    .trade-card {{ background: var(--panel-raised); border: 1px solid var(--rule); border-radius: 8px; padding: 16px; }}
    .trade-card-head {{ display: flex; align-items: center; gap: 8px; margin-bottom: 20px; }}
    .ticker {{ font-family: 'Space Grotesk', sans-serif; font-weight: 600; font-size: 15px; }}
    .dir-tag {{ font-size: 10px; letter-spacing: 0.06em; padding: 2px 7px; border-radius: 3px; font-weight: 600; }}
    .dir-long {{ background: rgba(127,182,158,0.16); color: var(--win); }}
    .dir-short {{ background: rgba(217,131,111,0.16); color: var(--loss); }}
    .rr-tag {{ margin-left: auto; font-size: 11px; color: var(--chalk-dim); }}

    .ladder {{ position: relative; height: 2px; margin: 0 4px 26px; background: var(--rule); border-radius: 1px; }}
    .ladder-tick {{
        position: absolute; top: -3px; width: 1px; height: 8px;
        background: var(--rule); transform: translateX(-50%);
    }}
    .ladder-marker {{ position: absolute; top: 50%; transform: translate(-50%, -50%); }}
    .ladder-marker.entry {{
        width: 10px; height: 10px; border-radius: 50%;
        background: var(--chalk); border: 2px solid var(--panel-raised);
    }}
    .ladder-marker.price {{
        width: 10px; height: 10px; border-radius: 50%;
        background: var(--marigold); box-shadow: 0 0 0 3px rgba(224,164,88,0.22);
    }}
    .ladder-labels {{ display: flex; justify-content: space-between; }}
    .ladder-labels span {{
        font-size: 10px; line-height: 1.5; color: var(--chalk-dim); letter-spacing: 0.03em;
    }}
    .ladder-labels .lbl-entry {{ color: var(--chalk); text-align: center; }}
    .ladder-labels .lbl-tp {{ text-align: right; }}
    .trade-card-foot {{ margin-top: 14px; font-size: 10.5px; color: var(--chalk-dim); }}

    .empty-state {{ color: var(--chalk-dim); font-size: 13px; line-height: 1.6; padding: 4px 2px; }}

    table {{ width: 100%; border-collapse: collapse; font-size: 12.5px; }}
    th {{
        text-align: left; font-size: 10px; letter-spacing: 0.07em; text-transform: uppercase;
        color: var(--chalk-dim); font-weight: 600; padding: 8px 8px; border-bottom: 1px solid var(--rule);
    }}
    td {{ padding: 10px 8px; border-bottom: 1px dashed var(--rule); vertical-align: top; }}
    tr:last-child td {{ border-bottom: none; }}
    tr:nth-child(even) td {{ background: rgba(255,255,255,0.015); }}
    .ticker-cell {{ font-weight: 600; }}
    .rationale {{ color: var(--chalk-dim); }}
    .empty-cell {{ color: var(--chalk-dim); text-align: center; padding: 26px 10px; line-height: 1.6; }}
    .pill {{ font-size: 10px; letter-spacing: 0.05em; padding: 3px 8px; border-radius: 20px; font-weight: 600; white-space: nowrap; }}
    .pill-win {{ background: rgba(127,182,158,0.16); color: var(--win); }}
    .pill-loss {{ background: rgba(217,131,111,0.16); color: var(--loss); }}
    .pill-open {{ background: rgba(224,164,88,0.16); color: var(--marigold); }}

    footer {{ color: var(--chalk-dim); font-size: 10.5px; text-align: center; padding-top: 10px; line-height: 1.6; }}
</style>
</head>
<body>
    <header class="masthead">
        <div class="masthead-row">
            <div>
                <p class="masthead-title">Displacement Desk</p>
                <div class="masthead-rule"></div>
                <p class="masthead-sub">15m displacement · FVG · H4 bias · min 1:3 R:R</p>
            </div>
            <span class="status-pill"><span class="pulse-dot"></span>Live</span>
        </div>
    </header>

    <div class="stat-strip">
        {stat_tiles}
        <div class="stat-tile">
            <span class="stat-value mono" id="countdown">--:--</span>
            <span class="stat-label">Next scan (est.)</span>
        </div>
    </div>

    <section class="panel">
        <div class="panel-title"><h2>Active setups</h2><span class="count">{open_count} open</span></div>
        {trades_html}
    </section>

    <section class="panel">
        <div class="panel-title"><h2>Trade journal</h2><span class="count">last {shown_count} of {total_count}</span></div>
        <table>
            <tr><th>Time</th><th>Asset</th><th>Type</th><th>Entry</th><th>Outcome</th><th>Rationale</th></tr>
            {journal_rows}
        </table>
    </section>

    <footer>Updated on each scheduled scan, roughly every 15 min · paper-trading simulation only — no live orders placed</footer>

    <script>
    (function() {{
        var lastScan = new Date("{last_scan_iso}");
        var intervalMs = 15 * 60 * 1000; // matches the cron trigger interval
        var el = document.getElementById('countdown');
        if (!el || isNaN(lastScan.getTime())) return;

        function tick() {{
            var target = new Date(lastScan.getTime() + intervalMs);
            var diff = target - new Date();
            if (diff <= 0) {{
                el.textContent = "due now";
                return;
            }}
            var mins = Math.floor(diff / 60000);
            var secs = Math.floor((diff % 60000) / 1000);
            el.textContent = String(mins).padStart(2, '0') + ':' + String(secs).padStart(2, '0');
        }}
        tick();
        setInterval(tick, 1000);
    }})();
    </script>
</body>
</html>
"""


def render_dashboard(active_trades, last_scan, last_scan_iso):
    logs = read_journal()
    wins = sum(1 for log in logs if log['Outcome'] == 'WIN')
    closed_count = sum(1 for log in logs if log['Outcome'] in ('WIN', 'LOSS'))
    shown = logs[:15]

    if active_trades:
        trades_html = '<div class="trade-grid">' + "".join(
            build_trade_card(sym, trade) for sym, trade in active_trades.items()
        ) + '</div>'
    else:
        trades_html = '<p class="empty-state">No setup clears the filters right now — displacement, FVG, H4 bias, and a 1:3 minimum all have to line up. Watching for the next 15-minute window.</p>'

    html = PAGE_TEMPLATE.format(
        stat_tiles=build_stat_tiles(len(active_trades), closed_count, wins, last_scan),
        open_count=len(active_trades),
        trades_html=trades_html,
        shown_count=len(shown),
        total_count=len(logs),
        journal_rows=build_journal_rows(shown),
        last_scan_iso=last_scan_iso,
    )

    os.makedirs(os.path.dirname(DASHBOARD_FILE), exist_ok=True)
    with open(DASHBOARD_FILE, 'w', encoding='utf-8') as f:
        f.write(html)


if __name__ == '__main__':
    trades, scan_time, scan_time_iso = run_scan_cycle()
    render_dashboard(trades, scan_time, scan_time_iso)
    print(f"Scan complete at {scan_time}. Open positions: {len(trades)}.")
