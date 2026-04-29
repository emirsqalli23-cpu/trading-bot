#!/usr/bin/env python3
"""Bilan quotidien : lit les 3 state_*.json et envoie un recap ntfy."""

import json, os, urllib.request
from datetime import datetime, timezone, timedelta

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "nice-lens-ogc-emir")
CAPITAL_START = 1000.0
MARKETS = [
    ("crypto", "🪙", "CRYPTO"),
    ("gold",   "🥇", "OR"),
    ("forex",  "💱", "FOREX"),
]

def load(market):
    path = f"state/state_{market}.json"
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)

def compute(state, since_iso):
    """Renvoie (capital, total_pnl, today_pnl, n_trades_today, n_wins_today)."""
    if not state:
        return None
    capital = state["capital"]
    trades  = state.get("trades", [])
    today   = [t for t in trades if t["time"] >= since_iso]
    wins    = [t for t in today if t["pnl"] > 0]
    return {
        "capital":      capital,
        "total_pnl":    round(capital - CAPITAL_START, 2),
        "today_pnl":    round(sum(t["pnl"] for t in today), 2),
        "today_trades": len(today),
        "today_wins":   len(wins),
        "total_trades": len(trades),
        "open_positions": len(state.get("positions", {})),
    }

def main():
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    lines = [f"📊 Bilan {datetime.now().strftime('%d/%m')} (24h)\n"]
    grand_today = 0.0
    grand_total = 0.0

    for key, emoji, label in MARKETS:
        state = load(key)
        if not state:
            lines.append(f"{emoji} {label} : pas encore actif")
            continue
        s = compute(state, since)
        wr_today = f"{s['today_wins']}/{s['today_trades']}" if s['today_trades'] else "-"
        sign_today = "+" if s['today_pnl'] >= 0 else ""
        sign_total = "+" if s['total_pnl'] >= 0 else ""
        lines.append(
            f"{emoji} {label}\n"
            f"  Capital : {s['capital']:.0f}$ ({sign_total}{s['total_pnl']:.0f}$)\n"
            f"  24h : {sign_today}{s['today_pnl']:.0f}$ | Trades: {wr_today} | Ouvertes: {s['open_positions']}"
        )
        grand_today += s['today_pnl']
        grand_total += s['total_pnl']

    sign_t = "+" if grand_today >= 0 else ""
    sign_g = "+" if grand_total >= 0 else ""
    lines.append(f"\n💰 Total 3 bots\n  24h : {sign_t}{grand_today:.0f}$ | Global : {sign_g}{grand_total:.0f}$")

    msg = "\n".join(lines)
    print(msg)

    payload = json.dumps({
        "topic": NTFY_TOPIC,
        "title": f"📊 Bilan trading {datetime.now().strftime('%d/%m')}",
        "message": msg,
        "priority": 3,
        "tags": ["bar_chart"],
    }).encode()
    try:
        urllib.request.urlopen(
            urllib.request.Request("https://ntfy.sh", data=payload,
                headers={"Content-Type": "application/json"}),
            timeout=10)
        print("✅ Notif envoyée")
    except Exception as e:
        print(f"⚠️ Notif KO : {e}")

if __name__ == "__main__":
    main()
