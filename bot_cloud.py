#!/usr/bin/env python3
"""
ICT/SMC Bot Cloud — Version GitHub Actions
Tourne toutes les 5 minutes même Mac éteint.
State persisté dans state/state.json (committé dans le repo).
"""

import json, os, urllib.request, urllib.parse
from datetime import datetime, timezone

# ── CONFIG ────────────────────────────────────────────────────────────────
SYMBOLS       = ["XBTUSD", "ETHUSD", "SOLUSD"]   # Noms Kraken
SYMBOLS_NICE  = {"XBTUSD": "BTC/USD", "ETHUSD": "ETH/USD", "SOLUSD": "SOL/USD"}
CAPITAL_START = 1000.0
RISK_PCT      = 0.02
MIN_RR        = 2.0
MAX_POSITIONS = 2
NTFY_TOPIC    = os.environ.get("NTFY_TOPIC", "nice-lens-ogc-emir")
STATE_FILE    = os.path.join(os.path.dirname(__file__), "state/state.json")

# ── STATE ─────────────────────────────────────────────────────────────────
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"capital": CAPITAL_START, "positions": {}, "trades": [], "timestamp": None}

def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    state["timestamp"] = datetime.now(timezone.utc).isoformat()
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

# ── NTFY ──────────────────────────────────────────────────────────────────
def notify(title, message, priority=4, tags=None):
    payload = json.dumps({
        "topic": NTFY_TOPIC,
        "title": title,
        "message": message,
        "priority": priority,
        "tags": tags or ["chart_with_upwards_trend"],
    }).encode()
    try:
        req = urllib.request.Request(
            "https://ntfy.sh",
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=10)
        print(f"📲 Notif envoyée : {title}")
    except Exception as e:
        print(f"⚠️ Notif échouée : {e}")

# ── KRAKEN OHLCV (urllib, pas de dépendances) ─────────────────────────────
def fetch_ohlcv(pair, interval, count=100):
    url = f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval={interval}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    resp = urllib.request.urlopen(req, timeout=15).read()
    data = json.loads(resp)
    if data.get("error"):
        raise ValueError(f"Kraken API error: {data['error']}")
    result_key = [k for k in data["result"] if k != "last"][0]
    rows = data["result"][result_key][-count:]
    return [{"ts": r[0], "open": float(r[1]), "high": float(r[2]),
             "low": float(r[3]), "close": float(r[4])} for r in rows]

def get_price(pair):
    url = f"https://api.kraken.com/0/public/Ticker?pair={pair}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    resp = urllib.request.urlopen(req, timeout=10).read()
    data = json.loads(resp)
    result_key = list(data["result"].keys())[0]
    return float(data["result"][result_key]["c"][0])

# ── ANALYSE ICT ───────────────────────────────────────────────────────────
def market_structure(candles, n=20):
    recent = candles[-n:]
    highs  = [c["high"]  for c in recent]
    lows   = [c["low"]   for c in recent]
    closes = [c["close"] for c in candles]

    swing_high = max(highs)
    swing_low  = min(lows)

    ema = closes[0]
    for c in closes[1:]:
        ema = c * (2 / 22) + ema * (1 - 2 / 22)

    last = closes[-1]
    hh = highs[-1] > max(highs[:-1]) if len(highs) > 1 else False
    hl = lows[-1]  > min(lows[:-1])  if len(lows) > 1  else False
    lh = highs[-1] < max(highs[:-1]) if len(highs) > 1 else False
    ll = lows[-1]  < min(lows[:-1])  if len(lows) > 1  else False

    if (hh or hl) and last > ema:
        trend = "BULLISH"
    elif (lh or ll) and last < ema:
        trend = "BEARISH"
    else:
        trend = "RANGE"

    return {"trend": trend, "swing_high": swing_high, "swing_low": swing_low, "ema21": ema}

def find_ob(candles):
    ob_bull = ob_bear = None
    for i in range(len(candles) - 3, max(len(candles) - 20, 0), -1):
        c0, c1 = candles[i], candles[i+1]
        if c0["close"] < c0["open"] and c1["close"] > c1["open"] and c1["close"] > c0["high"]:
            ob_bull = {"low": c0["low"], "high": c0["high"]}
            break
    for i in range(len(candles) - 3, max(len(candles) - 20, 0), -1):
        c0, c1 = candles[i], candles[i+1]
        if c0["close"] > c0["open"] and c1["close"] < c1["open"] and c1["close"] < c0["low"]:
            ob_bear = {"low": c0["low"], "high": c0["high"]}
            break
    return ob_bull, ob_bear

def check_mss(candles_m5):
    if len(candles_m5) < 15:
        return None
    recent = candles_m5[-10:]
    swing_high = max(c["high"]  for c in recent[:-1])
    swing_low  = min(c["low"]   for c in recent[:-1])
    last_close = candles_m5[-1]["close"]
    if last_close > swing_high:
        return "BULLISH_MSS"
    if last_close < swing_low:
        return "BEARISH_MSS"
    return None

def build_plan(direction, struct_h4, ob_bull, ob_bear, capital):
    if direction == "LONG":
        entry = ob_bull["low"] if ob_bull else struct_h4["ema21"]
        sl    = min(entry * 0.997, struct_h4["swing_low"] * 1.001)
        tp1   = struct_h4["swing_high"]
    else:
        entry = ob_bear["high"] if ob_bear else struct_h4["ema21"]
        sl    = max(entry * 1.003, struct_h4["swing_high"] * 0.999)
        tp1   = struct_h4["swing_low"]

    risk = abs(entry - sl)
    rr1  = round(abs(tp1 - entry) / risk, 2) if risk > 0 else 0
    return {"direction": direction, "entry": entry, "sl": sl,
            "tp1": tp1, "rr1": rr1, "valid": rr1 >= MIN_RR}

# ── CYCLE PRINCIPAL ───────────────────────────────────────────────────────
def run():
    state = load_state()
    capital   = state["capital"]
    positions = state["positions"]
    trades    = state["trades"]

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Capital: {capital:.2f}$ | Positions: {list(positions.keys())}")

    for sym in list(positions.keys()):
        pos = positions[sym]
        try:
            price = get_price(sym)
            nice  = SYMBOLS_NICE.get(sym, sym)
            hit_sl = (pos["direction"] == "LONG"  and price <= pos["sl"]) or \
                     (pos["direction"] == "SHORT" and price >= pos["sl"])
            hit_tp = (pos["direction"] == "LONG"  and price >= pos["tp1"]) or \
                     (pos["direction"] == "SHORT" and price <= pos["tp1"])

            if hit_sl:
                pnl      = -pos["risk_amount"]
                trades.append({"symbol": nice, "type": "SL", "pnl": pnl,
                                "time": datetime.now(timezone.utc).isoformat()})
                del positions[sym]
                notify(f"🔴 STOP-LOSS {nice}",
                       f"Prix: {price:.2f}$ | Perte: {pnl:.2f}$ | Capital: {capital:.2f}$",
                       priority=4, tags=["x"])
                print(f"🔴 SL {nice} | PnL: {pnl:.2f}$")

            elif hit_tp:
                pnl      = round(pos["risk_amount"] * pos["rr1"], 2)
                capital += pos["risk_amount"] + pnl
                trades.append({"symbol": nice, "type": "TP", "pnl": pnl,
                                "time": datetime.now(timezone.utc).isoformat()})
                del positions[sym]
                notify(f"🟢 TAKE-PROFIT {nice}",
                       f"Prix: {price:.2f}$ | Gain: +{pnl:.2f}$ | Capital: {capital:.2f}$",
                       priority=5, tags=["white_check_mark", "moneybag"])
                print(f"🟢 TP {nice} | PnL: +{pnl:.2f}$")
            else:
                print(f"⏳ {nice} en cours | Prix: {price:.2f}$ | SL: {pos['sl']:.2f} | TP1: {pos['tp1']:.2f}")
        except Exception as e:
            print(f"⚠️ Erreur gestion {sym}: {e}")

    for sym in SYMBOLS:
        nice = SYMBOLS_NICE.get(sym, sym)
        if sym in positions or len(positions) >= MAX_POSITIONS:
            continue
        try:
            c_h4 = fetch_ohlcv(sym, 240, 100)
            c_h1 = fetch_ohlcv(sym, 60, 50)
            c_m5 = fetch_ohlcv(sym, 5, 30)

            s_h4 = market_structure(c_h4)
            s_h1 = market_structure(c_h1, n=12)
            mss  = check_mss(c_m5)
            ob_bull, ob_bear = find_ob(c_h1)

            if s_h4["trend"] == "BULLISH" and s_h1["trend"] == "BULLISH":
                bias = "LONG"
            elif s_h4["trend"] == "BEARISH" and s_h1["trend"] == "BEARISH":
                bias = "SHORT"
            else:
                print(f"⏸ {nice} | H4:{s_h4['trend']} H1:{s_h1['trend']} → WAIT")
                continue

            expected_mss = "BULLISH_MSS" if bias == "LONG" else "BEARISH_MSS"
            if mss != expected_mss:
                print(f"⏳ {nice} | Biais:{bias} | MSS en attente (actuel: {mss})")
                continue

            plan = build_plan(bias, s_h4, ob_bull, ob_bear, capital)
            print(f"📋 {nice} | {bias} | Entrée:{plan['entry']:.2f} | R:R:{plan['rr1']}")

            if not plan["valid"]:
                print(f"❌ {nice} R:R {plan['rr1']} < {MIN_RR}")
                continue

            risk_amount = round(capital * RISK_PCT, 2)
            capital    -= risk_amount
            positions[sym] = {
                "direction":   plan["direction"],
                "entry":       plan["entry"],
                "sl":          plan["sl"],
                "tp1":         plan["tp1"],
                "rr1":         plan["rr1"],
                "risk_amount": risk_amount,
            }
            notify(
                f"🟢 LONG OUVERT {nice}" if bias == "LONG" else f"🔴 SHORT OUVERT {nice}",
                f"Entrée: {plan['entry']:.2f}$ | SL: {plan['sl']:.2f}$ | TP1: {plan['tp1']:.2f}$ | R:R: {plan['rr1']}",
                priority=4, tags=["rocket" if bias == "LONG" else "chart_with_downwards_trend"]
            )
            print(f"✅ POSITION OUVERTE {nice} {bias} | Risque: {risk_amount:.2f}$")

        except Exception as e:
            print(f"⚠️ Erreur analyse {sym}: {e}")

    state["capital"]   = capital
    state["positions"] = positions
    state["trades"]    = trades
    save_state(state)

    wins = [t for t in trades if t["pnl"] > 0]
    wr   = round(len(wins) / len(trades) * 100, 1) if trades else 0
    pnl  = round(capital - CAPITAL_START, 2)
    print(f"✅ État sauvegardé | Trades:{len(trades)} | WR:{wr}% | PnL:{pnl:+.2f}$ | Capital:{capital:.2f}$")

if __name__ == "__main__":
    run()