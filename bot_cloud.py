#!/usr/bin/env python3
"""
ICT/SMC Bot Cloud — Version GitHub Actions multi-marche.

MARKET (env) decide du marche trade :
  - crypto (defaut) : Kraken | BTC, ETH, SOL
  - gold            : Yahoo  | XAU/USD (or)
  - forex           : Yahoo  | EUR/USD, GBP/USD

Chaque marche a son propre state/state_<market>.json -> capital independant.
"""

import json, os, urllib.request, urllib.parse
from datetime import datetime, timezone

# ── CONFIG GLOBALE ────────────────────────────────────────────────────────
MARKET        = os.environ.get("MARKET", "crypto").lower()
CAPITAL_START = 1000.0
RISK_PCT      = 0.02
MIN_RR        = 2.0
MAX_POSITIONS = 2
NTFY_TOPIC    = os.environ.get("NTFY_TOPIC", "nice-lens-ogc-emir")

# ── CONFIG PAR MARCHE ─────────────────────────────────────────────────────
if MARKET == "gold":
    SYMBOLS      = ["GC=F"]
    SYMBOLS_NICE = {"GC=F": "Or"}
    DATA_SOURCE  = "yahoo"
    LABEL        = "OR"
    EMOJI        = "🥇"
elif MARKET == "forex":
    SYMBOLS      = ["EURUSD=X", "GBPUSD=X"]
    SYMBOLS_NICE = {"EURUSD=X": "EUR/USD", "GBPUSD=X": "GBP/USD"}
    DATA_SOURCE  = "yahoo"
    LABEL        = "FOREX"
    EMOJI        = "💱"
else:  # crypto par defaut
    MARKET       = "crypto"
    SYMBOLS      = ["XBTUSD", "ETHUSD", "SOLUSD"]
    SYMBOLS_NICE = {"XBTUSD": "BTC/USD", "ETHUSD": "ETH/USD", "SOLUSD": "SOL/USD"}
    DATA_SOURCE  = "kraken"
    LABEL        = "CRYPTO"
    EMOJI        = "🪙"

STATE_FILE = os.path.join(os.path.dirname(__file__), f"state/state_{MARKET}.json")

# ── STATE ─────────────────────────────────────────────────────────────────
def load_state():
    # Migration : si l'ancien state.json existe et qu'on est en crypto, le reprendre
    legacy = os.path.join(os.path.dirname(__file__), "state/state.json")
    if MARKET == "crypto" and not os.path.exists(STATE_FILE) and os.path.exists(legacy):
        try:
            with open(legacy) as f: data = json.load(f)
            print(f"📦 Migration : reprise du state legacy ({data.get('capital','?')}$)")
            return data
        except Exception:
            pass
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
    state["market"]    = MARKET
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

# ── NTFY ──────────────────────────────────────────────────────────────────
def notify(title, message, priority=4, tags=None):
    payload = json.dumps({
        "topic": NTFY_TOPIC,
        "title": f"{EMOJI} [{LABEL}] {title}",
        "message": message,
        "priority": priority,
        "tags": tags or ["chart_with_upwards_trend"],
    }).encode()
    try:
        req = urllib.request.Request(
            "https://ntfy.sh", data=payload,
            headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=10)
        print(f"📲 Notif : {title}")
    except Exception as e:
        print(f"⚠️ Notif KO : {e}")

# ── SOURCE 1 : KRAKEN (crypto) ────────────────────────────────────────────
def fetch_kraken(pair, interval, count=100):
    url = f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval={interval}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    data = json.loads(urllib.request.urlopen(req, timeout=15).read())
    if data.get("error"):
        raise ValueError(f"Kraken: {data['error']}")
    result_key = [k for k in data["result"] if k != "last"][0]
    rows = data["result"][result_key][-count:]
    return [{"ts": r[0], "open": float(r[1]), "high": float(r[2]),
             "low": float(r[3]), "close": float(r[4])} for r in rows]

def get_kraken_price(pair):
    url = f"https://api.kraken.com/0/public/Ticker?pair={pair}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    data = json.loads(urllib.request.urlopen(req, timeout=10).read())
    result_key = list(data["result"].keys())[0]
    return float(data["result"][result_key]["c"][0])

# ── SOURCE 2 : YAHOO FINANCE (or, forex, indices) ─────────────────────────
def fetch_yahoo(symbol, interval, range_str, count=100):
    """interval: 5m, 15m, 60m, 1d | range: 5d, 30d, 60d"""
    sym_enc = urllib.parse.quote(symbol, safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym_enc}?interval={interval}&range={range_str}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    data = json.loads(urllib.request.urlopen(req, timeout=15).read())
    if data["chart"].get("error"):
        raise ValueError(f"Yahoo: {data['chart']['error']}")
    result = data["chart"]["result"][0]
    ts = result.get("timestamp", [])
    q  = result["indicators"]["quote"][0]
    candles = []
    for i, t in enumerate(ts):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c): continue
        candles.append({"ts": t, "open": o, "high": h, "low": l, "close": c})
    return candles[-count:]

def aggregate_h4(h1_candles):
    """Combine 4 bougies H1 -> 1 bougie H4."""
    h4 = []
    for i in range(0, len(h1_candles) - 3, 4):
        chunk = h1_candles[i:i+4]
        h4.append({
            "ts":    chunk[0]["ts"],
            "open":  chunk[0]["open"],
            "high":  max(c["high"]  for c in chunk),
            "low":   min(c["low"]   for c in chunk),
            "close": chunk[-1]["close"],
        })
    return h4

def get_yahoo_price(symbol):
    sym_enc = urllib.parse.quote(symbol, safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym_enc}?interval=5m&range=1d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    data = json.loads(urllib.request.urlopen(req, timeout=10).read())
    meta = data["chart"]["result"][0]["meta"]
    return float(meta.get("regularMarketPrice") or meta.get("previousClose"))

# ── DISPATCHERS GENERIQUES ────────────────────────────────────────────────
def fetch_candles(sym, tf):
    """tf in ('H4','H1','M5')"""
    if DATA_SOURCE == "kraken":
        intervals = {"H4": 240, "H1": 60, "M5": 5}
        counts    = {"H4": 100, "H1": 50, "M5": 30}
        return fetch_kraken(sym, intervals[tf], counts[tf])
    # yahoo
    if tf == "H4":
        return aggregate_h4(fetch_yahoo(sym, "60m", "30d", 400))
    if tf == "H1":
        return fetch_yahoo(sym, "60m", "15d", 50)
    if tf == "M5":
        return fetch_yahoo(sym, "5m", "5d", 30)

def get_price(sym):
    return get_kraken_price(sym) if DATA_SOURCE == "kraken" else get_yahoo_price(sym)

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
        ema = c * (2/22) + ema * (1 - 2/22)

    last = closes[-1]
    hh = highs[-1] > max(highs[:-1]) if len(highs) > 1 else False
    hl = lows[-1]  > min(lows[:-1])  if len(lows)  > 1 else False
    lh = highs[-1] < max(highs[:-1]) if len(highs) > 1 else False
    ll = lows[-1]  < min(lows[:-1])  if len(lows)  > 1 else False

    if (hh or hl) and last > ema:   trend = "BULLISH"
    elif (lh or ll) and last < ema: trend = "BEARISH"
    else:                           trend = "RANGE"
    return {"trend": trend, "swing_high": swing_high, "swing_low": swing_low, "ema21": ema}

def find_ob(candles):
    ob_bull = ob_bear = None
    for i in range(len(candles) - 3, max(len(candles) - 20, 0), -1):
        c0, c1 = candles[i], candles[i+1]
        if c0["close"] < c0["open"] and c1["close"] > c1["open"] and c1["close"] > c0["high"]:
            ob_bull = {"low": c0["low"], "high": c0["high"]}; break
    for i in range(len(candles) - 3, max(len(candles) - 20, 0), -1):
        c0, c1 = candles[i], candles[i+1]
        if c0["close"] > c0["open"] and c1["close"] < c1["open"] and c1["close"] < c0["low"]:
            ob_bear = {"low": c0["low"], "high": c0["high"]}; break
    return ob_bull, ob_bear

def check_mss(candles_m5):
    if len(candles_m5) < 15: return None
    recent = candles_m5[-10:]
    sh = max(c["high"] for c in recent[:-1])
    sl = min(c["low"]  for c in recent[:-1])
    last_close = candles_m5[-1]["close"]
    if last_close > sh: return "BULLISH_MSS"
    if last_close < sl: return "BEARISH_MSS"
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
    state     = load_state()
    capital   = state["capital"]
    positions = state["positions"]
    trades    = state["trades"]

    print(f"[{datetime.now().strftime('%H:%M:%S')}] [{LABEL}] Capital: {capital:.2f}$ | Positions: {list(positions.keys())}")

    # 1. Gerer les positions ouvertes
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
                pnl = -pos["risk_amount"]
                trades.append({"symbol": nice, "type": "SL", "pnl": pnl,
                               "time": datetime.now(timezone.utc).isoformat()})
                del positions[sym]
                notify(f"❌ Pari perdu sur {nice}",
                       f"Le bot s'est trompé.\nPerte : {abs(pnl):.0f}$\nArgent restant : {capital:.0f}$",
                       priority=4, tags=["x"])
                print(f"🔴 SL {nice} | PnL: {pnl:.2f}$")
            elif hit_tp:
                pnl = round(pos["risk_amount"] * pos["rr1"], 2)
                capital += pos["risk_amount"] + pnl
                trades.append({"symbol": nice, "type": "TP", "pnl": pnl,
                               "time": datetime.now(timezone.utc).isoformat()})
                del positions[sym]
                notify(f"✅ Pari gagné sur {nice} 💰",
                       f"Le bot avait raison !\nGain : +{pnl:.0f}$\nArgent total : {capital:.0f}$",
                       priority=5, tags=["white_check_mark", "moneybag"])
                print(f"🟢 TP {nice} | PnL: +{pnl:.2f}$")
            else:
                print(f"⏳ {nice} | Prix: {price:.4f} | SL: {pos['sl']:.4f} | TP1: {pos['tp1']:.4f}")
        except Exception as e:
            print(f"⚠️ Erreur gestion {sym}: {e}")

    # 2. Chercher nouveaux setups
    for sym in SYMBOLS:
        nice = SYMBOLS_NICE.get(sym, sym)
        if sym in positions or len(positions) >= MAX_POSITIONS:
            continue
        try:
            c_h4 = fetch_candles(sym, "H4")
            c_h1 = fetch_candles(sym, "H1")
            c_m5 = fetch_candles(sym, "M5")

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
                print(f"⏳ {nice} | Biais:{bias} | MSS attendu:{expected_mss} | actuel:{mss}")
                continue

            plan = build_plan(bias, s_h4, ob_bull, ob_bear, capital)
            print(f"📋 {nice} | {bias} | Entrée:{plan['entry']:.4f} | R:R:{plan['rr1']}")

            if not plan["valid"]:
                print(f"❌ {nice} R:R {plan['rr1']} < {MIN_RR}")
                continue

            risk_amount = round(capital * RISK_PCT, 2)
            capital -= risk_amount
            positions[sym] = {
                "direction":   plan["direction"],
                "entry":       plan["entry"],
                "sl":          plan["sl"],
                "tp1":         plan["tp1"],
                "rr1":         plan["rr1"],
                "risk_amount": risk_amount,
            }
            direction_fr = "va monter 📈" if bias == "LONG" else "va baisser 📉"
            notify(f"🎯 Nouveau pari sur {nice}",
                   f"Le bot pense que {nice} {direction_fr}\nMise : {risk_amount:.0f}$\nSi ça marche : +{round(risk_amount * plan['rr1']):.0f}$",
                   priority=4, tags=["rocket" if bias == "LONG" else "chart_with_downwards_trend"])
            print(f"✅ POSITION OUVERTE {nice} {bias} | Risque: {risk_amount:.2f}$")
        except Exception as e:
            print(f"⚠️ Erreur analyse {sym}: {e}")

    # 3. Sauvegarder
    state["capital"]   = capital
    state["positions"] = positions
    state["trades"]    = trades
    save_state(state)

    wins = [t for t in trades if t["pnl"] > 0]
    wr   = round(len(wins) / len(trades) * 100, 1) if trades else 0
    pnl  = round(capital - CAPITAL_START, 2)
    print(f"✅ [{LABEL}] State sauve | Trades:{len(trades)} | WR:{wr}% | PnL:{pnl:+.2f}$ | Capital:{capital:.2f}$")

if __name__ == "__main__":
    run()
