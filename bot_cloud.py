#!/usr/bin/env python3
"""
ICT/SMC Bot Cloud — VERSION PRO (multi-marche + news + trade management).

MARKET (env) :
  - crypto : Kraken | BTC, ETH, SOL
  - gold   : Yahoo  | XAU/USD
  - forex  : Yahoo  | EUR/USD, GBP/USD

Features :
  ✓ Filtre news (calendrier eco + RSS shock)
  ✓ Breakeven : SL → entrée à mi-chemin
  ✓ TP partiel : 50% au TP1
  ✓ Trailing stop : SL suit le prix
  ✓ Filtre sessions (Londres+NY pour forex/or)
  ✓ Filtre corrélation (EUR+GBP en même direction interdit)
  ✓ ATR : taille adaptée à la volatilité
"""

import json, os, urllib.request, urllib.parse
from datetime import datetime, timezone

# Modules optionnels (le bot fonctionne meme sans)
try:
    from news_filter import can_open_position, should_close_positions
    NEWS_OK = True
except Exception as _e:
    print(f"⚠️ News filter indisponible : {_e}")
    NEWS_OK = False
    def can_open_position(sym): return True, None
    def should_close_positions(sym): return False, None

try:
    from trade_manager import (
        maybe_set_breakeven, maybe_take_partial, update_trailing_stop,
        compute_atr, adjust_risk_by_atr, in_active_session, has_correlated_position,
        daily_loss_exceeded, in_killzone, daily_bias, aligned_with_daily,
        fetch_dxy_trend, dxy_aligned,
        compute_adx, market_regime,
        fetch_cot_sentiment, cot_aligned,
        fetch_10y_yield_trend, yields_aligned,
    )
    TM_OK = True
except Exception as _e:
    print(f"⚠️ Trade manager indisponible : {_e}")
    TM_OK = False

# ── IG Markets broker (optionnel — DRY_RUN par défaut, pas d'ordre réel) ──
try:
    from ig_broker import IGBroker, bot_symbol_to_epic
    IG_OK = True
except Exception as _e:
    print(f"⚠️ IG broker indisponible : {_e}")
    IG_OK = False
IG_ENABLED = os.environ.get("IG_ENABLED", "false").lower() == "true"
IG_DRY_RUN = os.environ.get("IG_DRY_RUN", "true").lower() != "false"
IG_ENV     = os.environ.get("IG_ENV", "live")

# ── Conditions réelles de trading (spread + slippage simulés) ─────────────
try:
    from real_costs import apply_realistic_entry, apply_realistic_exit, get_spread_value
    REAL_COSTS_OK = True
except Exception as _e:
    print(f"⚠️ Real costs indisponible : {_e}")
    REAL_COSTS_OK = False
REAL_COSTS_ENABLED = os.environ.get("REAL_COSTS_ENABLED", "true").lower() == "true"

# ── Signaux sociaux (Fear & Greed + Trump shock detection) ────────────────
try:
    from social_signals import (
        fetch_crypto_fear_greed, fear_greed_signal_for_crypto,
        detect_market_shock_social,
    )
    SOCIAL_OK = True
except Exception as _e:
    print(f"⚠️ Social signals indisponible : {_e}")
    SOCIAL_OK = False

# ── CONFIG GLOBALE ────────────────────────────────────────────────────────
MARKET        = os.environ.get("MARKET", "crypto").lower()
CAPITAL_START = 1000.0
RISK_PCT      = 0.02
MIN_RR        = 1.8 if os.environ.get("MARKET", "crypto").lower() == "forex" else 2.0
MAX_POSITIONS = 2
NTFY_TOPIC    = os.environ.get("NTFY_TOPIC", "nice-lens-ogc-emir")

# TwelveData : données temps réel pour or + forex (remplace Yahoo Finance)
TWELVE_API_KEY = os.environ.get("TWELVE_API_KEY", "86757c28a7e3491ba6aa12f59aa13065")

if MARKET == "gold":
    SYMBOLS, SYMBOLS_NICE = ["GC=F"], {"GC=F": "Or"}
    DATA_SOURCE, LABEL, EMOJI = "yahoo", "OR", "🥇"
elif MARKET == "forex":
    # 4 paires : 2 majors USD + 2 cross JPY (non affectées par DXY/yields)
    SYMBOLS = ["EURUSD=X", "GBPUSD=X", "EURJPY=X", "GBPJPY=X"]
    SYMBOLS_NICE = {
        "EURUSD=X": "EUR/USD", "GBPUSD=X": "GBP/USD",
        "EURJPY=X": "EUR/JPY", "GBPJPY=X": "GBP/JPY",
    }
    DATA_SOURCE, LABEL, EMOJI = "yahoo", "FOREX", "💱"
else:
    MARKET = "crypto"
    SYMBOLS = ["XBTUSD", "ETHUSD", "SOLUSD"]
    SYMBOLS_NICE = {"XBTUSD": "BTC/USD", "ETHUSD": "ETH/USD", "SOLUSD": "SOL/USD"}
    DATA_SOURCE, LABEL, EMOJI = "kraken", "CRYPTO", "🪙"

STATE_FILE = os.path.join(os.path.dirname(__file__), f"state/state_{MARKET}.json")

# ── STATE ─────────────────────────────────────────────────────────────────
def load_state():
    legacy = os.path.join(os.path.dirname(__file__), "state/state.json")
    if MARKET == "crypto" and not os.path.exists(STATE_FILE) and os.path.exists(legacy):
        try:
            with open(legacy) as f: data = json.load(f)
            print(f"📦 Migration legacy ({data.get('capital','?')}$)")
            return data
        except Exception: pass
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f: return json.load(f)
        except Exception: pass
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
        req = urllib.request.Request("https://ntfy.sh", data=payload,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
        print(f"📲 {title}")
    except Exception as e:
        print(f"⚠️ Notif KO : {e}")

# ── DATA SOURCES ──────────────────────────────────────────────────────────
def fetch_kraken(pair, interval, count=100):
    url = f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval={interval}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    data = json.loads(urllib.request.urlopen(req, timeout=15).read())
    if data.get("error"): raise ValueError(f"Kraken: {data['error']}")
    rk = [k for k in data["result"] if k != "last"][0]
    rows = data["result"][rk][-count:]
    return [{"ts": r[0], "open": float(r[1]), "high": float(r[2]),
             "low": float(r[3]), "close": float(r[4])} for r in rows]

def get_kraken_price(pair):
    url = f"https://api.kraken.com/0/public/Ticker?pair={pair}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    data = json.loads(urllib.request.urlopen(req, timeout=10).read())
    return float(data["result"][list(data["result"].keys())[0]]["c"][0])

def fetch_twelvedata(symbol, interval, count=100):
    """
    TwelveData : données OHLCV temps réel (remplace Yahoo Finance).
    Symbols : XAU/USD pour l'or, EUR/USD et GBP/USD pour le forex.
    Avantage vs Yahoo : pas de délai 15-20 min, API officielle stable.
    """
    # Mapping symbols Yahoo → TwelveData
    sym_map = {
        "GC=F":     "XAU/USD",
        "EURUSD=X": "EUR/USD",
        "GBPUSD=X": "GBP/USD",
    }
    td_sym = sym_map.get(symbol, symbol)
    td_sym_enc = urllib.parse.quote(td_sym, safe="")
    # Mapping intervalles
    iv_map = {"60m": "1h", "5m": "5min", "1d": "1day"}
    td_iv = iv_map.get(interval, interval)
    url = (f"https://api.twelvedata.com/time_series"
           f"?symbol={td_sym_enc}&interval={td_iv}&outputsize={count}"
           f"&apikey={TWELVE_API_KEY}&timezone=UTC&order=ASC")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    data = json.loads(urllib.request.urlopen(req, timeout=15).read())
    if data.get("status") == "error":
        raise ValueError(f"TwelveData: {data.get('message','?')}")
    candles = []
    for bar in data.get("values", []):
        try:
            candles.append({
                "ts":    bar["datetime"],
                "open":  float(bar["open"]),
                "high":  float(bar["high"]),
                "low":   float(bar["low"]),
                "close": float(bar["close"]),
            })
        except Exception:
            continue
    return candles

def get_twelvedata_price(symbol):
    sym_map = {"GC=F": "XAU/USD", "EURUSD=X": "EUR/USD", "GBPUSD=X": "GBP/USD"}
    td_sym = urllib.parse.quote(sym_map.get(symbol, symbol), safe="")
    url = f"https://api.twelvedata.com/price?symbol={td_sym}&apikey={TWELVE_API_KEY}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    data = json.loads(urllib.request.urlopen(req, timeout=10).read())
    if data.get("status") == "error":
        raise ValueError(f"TwelveData price: {data.get('message','?')}")
    return float(data["price"])

def fetch_yahoo(symbol, interval, range_str, count=100):
    """Fallback Yahoo Finance (utilisé uniquement si TwelveData KO)."""
    sym_enc = urllib.parse.quote(symbol, safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym_enc}?interval={interval}&range={range_str}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    data = json.loads(urllib.request.urlopen(req, timeout=15).read())
    if data["chart"].get("error"): raise ValueError(f"Yahoo: {data['chart']['error']}")
    result = data["chart"]["result"][0]
    ts = result.get("timestamp", [])
    q  = result["indicators"]["quote"][0]
    candles = []
    for i, t in enumerate(ts):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c): continue
        candles.append({"ts": t, "open": o, "high": h, "low": l, "close": c})
    return candles[-count:]

def aggregate_h4(h1):
    out = []
    for i in range(0, len(h1) - 3, 4):
        chunk = h1[i:i+4]
        out.append({"ts": chunk[0]["ts"], "open": chunk[0]["open"],
                    "high": max(c["high"] for c in chunk),
                    "low":  min(c["low"]  for c in chunk),
                    "close": chunk[-1]["close"]})
    return out

def get_yahoo_price(symbol):
    sym_enc = urllib.parse.quote(symbol, safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym_enc}?interval=5m&range=1d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    data = json.loads(urllib.request.urlopen(req, timeout=10).read())
    meta = data["chart"]["result"][0]["meta"]
    return float(meta.get("regularMarketPrice") or meta.get("previousClose"))

def fetch_candles(sym, tf):
    if DATA_SOURCE == "kraken":
        intervals = {"H4": 240, "H1": 60, "M5": 5}
        counts    = {"H4": 100, "H1": 50, "M5": 30}
        return fetch_kraken(sym, intervals[tf], counts[tf])
    # TwelveData pour or/forex (temps réel), fallback Yahoo si KO
    iv_map = {"H4": "60m", "H1": "60m", "M5": "5m"}
    counts = {"H4": 400, "H1": 50, "M5": 30}
    try:
        candles = fetch_twelvedata(sym, iv_map[tf], counts[tf])
        if tf == "H4":
            candles = aggregate_h4(candles)
        if len(candles) < 5:
            raise ValueError("Pas assez de bougies TwelveData")
        return candles
    except Exception as e:
        print(f"  ⚠️ TwelveData KO ({e}), fallback Yahoo")
        if tf == "H4": return aggregate_h4(fetch_yahoo(sym, "60m", "30d", 400))
        if tf == "H1": return fetch_yahoo(sym, "60m", "15d", 50)
        if tf == "M5": return fetch_yahoo(sym, "5m", "5d", 30)

def get_price(sym):
    if DATA_SOURCE == "kraken":
        return get_kraken_price(sym)
    try:
        return get_twelvedata_price(sym)
    except Exception as e:
        print(f"  ⚠️ TwelveData price KO ({e}), fallback Yahoo")
        return get_yahoo_price(sym)

def fetch_yahoo_d1(sym, count=100):
    """D1 via Yahoo uniquement (TwelveData consomme trop de crédits sur 1d)."""
    return fetch_yahoo(sym, "1d", "200d", count)

# ── ANALYSE ICT ───────────────────────────────────────────────────────────
def market_structure(candles, n=20):
    recent = candles[-n:]
    highs  = [c["high"] for c in recent]
    lows   = [c["low"]  for c in recent]
    closes = [c["close"] for c in candles]
    swing_high, swing_low = max(highs), min(lows)
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
    last = candles_m5[-1]["close"]
    if last > sh: return "BULLISH_MSS"
    if last < sl: return "BEARISH_MSS"
    return None

def build_plan(direction, struct_h4, ob_bull, ob_bear, atr_h1=None):
    """
    Construit le plan de trade : entrée, SL, TP, R:R.

    Or (gold) : SL basé sur l'ATR H1 (volatilité réelle) plutôt qu'un % fixe.
    Pourquoi ? L'or bouge facilement 10-20$/oz en une heure. Un SL à 0.3% fixe
    est trop serré → stoppé avant que le mouvement parte, R:R jamais atteint.
    ATR × 1.8 = buffer suffisant pour rester dans le trade.
    """
    if direction == "LONG":
        entry = ob_bull["low"] if ob_bull else struct_h4["ema21"]
        if MARKET == "gold" and atr_h1:
            sl = entry - 1.8 * atr_h1          # SL adapté à la vraie volatilité Or
        else:
            sl = min(entry * 0.997, struct_h4["swing_low"] * 1.001)
        tp1 = struct_h4["swing_high"]
    else:
        entry = ob_bear["high"] if ob_bear else struct_h4["ema21"]
        if MARKET == "gold" and atr_h1:
            sl = entry + 1.8 * atr_h1
        else:
            sl = max(entry * 1.003, struct_h4["swing_high"] * 0.999)
        tp1 = struct_h4["swing_low"]
    risk = abs(entry - sl)
    rr1  = round(abs(tp1 - entry) / risk, 2) if risk > 0 else 0
    return {"direction": direction, "entry": entry, "sl": sl,
            "tp1": tp1, "rr1": rr1, "valid": rr1 >= MIN_RR}

# ── PNL HELPER (gere positions partielles) ────────────────────────────────
def pnl_at_price(pos, exit_price):
    """PnL sur la position restante, en se basant sur le SL initial."""
    initial_sl  = pos.get("initial_sl", pos["sl"])
    initial_r   = abs(pos["entry"] - initial_sl)
    if initial_r == 0: return 0.0
    if pos["direction"] == "LONG":
        r_mult = (exit_price - pos["entry"]) / initial_r
    else:
        r_mult = (pos["entry"] - exit_price) / initial_r
    return round(pos["risk_amount"] * r_mult, 2)

# ── CYCLE PRINCIPAL ───────────────────────────────────────────────────────
def run():
    state     = load_state()
    capital   = state["capital"]
    positions = state["positions"]
    trades    = state["trades"]

    # Log du cycle pour le dashboard
    cycle_log = {
        "time": datetime.now(timezone.utc).isoformat(),
        "checks": {},
        "symbols": [],
        "actions": [],
    }

    print(f"[{datetime.now().strftime('%H:%M:%S')}] [{LABEL}] Capital:{capital:.2f}$ | Pos:{list(positions.keys())} | News:{NEWS_OK} | TM:{TM_OK}")

    # ─── 0bis. SHOCK SOCIAL (Trump posts) — fermeture d'urgence ────────
    if SOCIAL_OK and positions:
        try:
            social_shock, social_msg = detect_market_shock_social(60)
            if social_shock:
                print(f"🚨 Shock social détecté : {social_msg}")
                for sym in list(positions.keys()):
                    pos = positions[sym]
                    price = get_price(sym)
                    pnl = pnl_at_price(pos, price)
                    capital += pos["risk_amount"] + pnl
                    trades.append({"symbol": SYMBOLS_NICE.get(sym, sym), "type": "TRUMP_SHOCK_EXIT",
                                   "pnl": pnl, "time": datetime.now(timezone.utc).isoformat(),
                                   "news": social_msg[:120]})
                    del positions[sym]
                    notify(f"🚨 Sortie sociale {SYMBOLS_NICE.get(sym, sym)}",
                           f"{social_msg[:100]}\nP&L : {pnl:+.0f}€",
                           priority=5, tags=["rotating_light"])
        except Exception as e:
            print(f"⚠️ Social shock check : {e}")

    # ─── 0. SHOCK NEWS : sortie d'urgence ─────────────────────────────────
    for sym in list(positions.keys()):
        try:
            shock, title = should_close_positions(sym)
            if shock:
                pos = positions[sym]
                price = get_price(sym)
                pnl = pnl_at_price(pos, price)
                capital += pos["risk_amount"] + pnl
                trades.append({"symbol": SYMBOLS_NICE.get(sym, sym), "type": "SHOCK_EXIT",
                               "pnl": pnl, "time": datetime.now(timezone.utc).isoformat(),
                               "news": title[:120]})
                del positions[sym]
                notify(f"🚨 Sortie d'urgence sur {SYMBOLS_NICE.get(sym, sym)}",
                       f"News : \"{title[:80]}\"\nP&L : {pnl:+.0f}$ | Capital : {capital:.0f}$",
                       priority=5, tags=["rotating_light"])
        except Exception as e:
            print(f"⚠️ Shock check {sym}: {e}")

    # ─── 1. GERER POSITIONS OUVERTES (BE, partial, trailing, SL/TP) ──────
    for sym in list(positions.keys()):
        pos = positions[sym]
        try:
            price = get_price(sym)
            nice  = SYMBOLS_NICE.get(sym, sym)

            # 1a. BREAKEVEN (SL → entrée si mi-chemin)
            if TM_OK:
                be_done, be_msg = maybe_set_breakeven(pos, price)
                if be_done:
                    notify(f"🛡️ Breakeven sur {nice}",
                           f"{be_msg}\nLe pire cas est maintenant 0$",
                           priority=3, tags=["shield"])

            # 1b. TP PARTIEL (50% sécurisé au TP1)
            if TM_OK:
                partial_pnl, partial_msg = maybe_take_partial(pos, price)
                if partial_pnl is not None:
                    # On reverse au capital : le 50% recupere + le 50% gagne
                    initial_risk = pos.get("initial_risk_amount", pos["risk_amount"] * 2)
                    half_recovered = round(0.5 * initial_risk, 2)
                    capital += half_recovered + partial_pnl
                    trades.append({"symbol": nice, "type": "TP_PARTIAL", "pnl": partial_pnl,
                                   "time": datetime.now(timezone.utc).isoformat()})
                    notify(f"💰 50% encaissé sur {nice}",
                           f"{partial_msg}\nCapital : {capital:.0f}$",
                           priority=5, tags=["moneybag"])

            # 1c. TRAILING STOP (SL suit le prix si actif)
            if TM_OK:
                trail_done, new_sl = update_trailing_stop(pos, price)

            # 1d. Vérifier SL / TP final
            hit_sl = (pos["direction"] == "LONG"  and price <= pos["sl"]) or \
                     (pos["direction"] == "SHORT" and price >= pos["sl"])
            hit_tp = (pos["direction"] == "LONG"  and price >= pos["tp1"]) or \
                     (pos["direction"] == "SHORT" and price <= pos["tp1"])

            if hit_sl:
                pnl = pnl_at_price(pos, pos["sl"])
                capital += pos["risk_amount"] + pnl

                if pos.get("tp1_taken"):
                    # Sortie post-TP1 : profit additionnel verrouillé
                    msg = (f"Trailing stop touché — gain bloqué\nP&L sur le runner : {pnl:+.0f}$\n"
                           f"Capital total : {capital:.0f}$")
                    notify(f"🎯 Runner clôturé sur {nice}", msg,
                           priority=4, tags=["chart_with_upwards_trend"])
                    trades.append({"symbol": nice, "type": "TRAIL_EXIT", "pnl": pnl,
                                   "time": datetime.now(timezone.utc).isoformat()})
                elif pos.get("be_set"):
                    # SL au breakeven : 0$ perdu
                    notify(f"⚪ Pari nul sur {nice}",
                           f"SL au breakeven, ni gain ni perte\nCapital : {capital:.0f}$",
                           priority=3, tags=["white_circle"])
                    trades.append({"symbol": nice, "type": "BE", "pnl": pnl,
                                   "time": datetime.now(timezone.utc).isoformat()})
                else:
                    # SL classique
                    notify(f"❌ Pari perdu sur {nice}",
                           f"Le bot s'est trompé.\nPerte : {abs(pnl):.0f}$\nCapital : {capital:.0f}$",
                           priority=4, tags=["x"])
                    trades.append({"symbol": nice, "type": "SL", "pnl": pnl,
                                   "time": datetime.now(timezone.utc).isoformat()})
                del positions[sym]

            elif hit_tp:
                # TP étendu touché (apres partial déjà pris)
                pnl = pnl_at_price(pos, pos["tp1"])
                capital += pos["risk_amount"] + pnl
                trades.append({"symbol": nice, "type": "TP_EXTENDED", "pnl": pnl,
                               "time": datetime.now(timezone.utc).isoformat()})
                del positions[sym]
                notify(f"✅ Pari gagné sur {nice} 💰",
                       f"TP étendu touché !\nGain : +{pnl:.0f}$\nCapital total : {capital:.0f}$",
                       priority=5, tags=["white_check_mark", "moneybag"])
            else:
                print(f"⏳ {nice} | Prix:{price:.4f} | SL:{pos['sl']:.4f} | TP:{pos['tp1']:.4f} | BE:{pos.get('be_set',False)} | Trail:{pos.get('trail_active',False)}")
        except Exception as e:
            print(f"⚠️ Erreur gestion {sym}: {e}")

    # ─── 2. CHERCHER NOUVEAUX SETUPS ─────────────────────────────────────
    # Filtre session global
    if TM_OK:
        ok_session, why = in_active_session(MARKET)
        if not ok_session:
            print(f"🛌 {why} — pas de nouveau trade ce cycle")
            state["capital"]   = capital
            state["positions"] = positions
            state["trades"]    = trades
            save_state(state)
            return

        cycle_log["checks"]["session"] = {"ok": True, "reason": "Sessions actives"}

        # FEATURE 1 : limite de perte quotidienne
        loss_blocked, today_pct = daily_loss_exceeded(state, CAPITAL_START)
        cycle_log["checks"]["daily_loss"] = {"ok": not loss_blocked, "pct": today_pct}
        if loss_blocked:
            msg = f"-{abs(today_pct)}% sur la journée — pause jusqu'à demain"
            print(f"🛑 {msg}")
            if not state.get("daily_lock_notified"):
                notify(f"🛑 Pause journée [{LABEL}]",
                       f"Le bot a perdu {today_pct}% aujourd'hui.\nIl arrête les nouveaux trades jusqu'à demain pour protéger le capital.",
                       priority=4, tags=["octagonal_sign"])
                state["daily_lock_notified"] = True
            cycle_log["status"] = "DAILY_LOSS_LOCK"
            state["capital"]   = capital
            state["positions"] = positions
            state["trades"]    = trades
            state["last_cycle"] = cycle_log
            save_state(state)
            return
        # Reset le flag à minuit
        if state.get("daily_lock_notified") and today_pct > -1:
            state["daily_lock_notified"] = False

        # FEATURE 2 : Killzones ICT
        in_kz, kz_name = in_killzone(MARKET)
        cycle_log["checks"]["killzone"] = {"ok": in_kz, "name": kz_name or f"hors zone ({datetime.now(timezone.utc).hour}h UTC)"}
        if not in_kz:
            now_h = datetime.now(timezone.utc).hour
            print(f"⏰ Hors killzone ({now_h}h UTC) — pas de nouveau trade")
            cycle_log["status"] = "WAIT_KILLZONE"
            state["capital"]   = capital
            state["positions"] = positions
            state["trades"]    = trades
            state["last_cycle"] = cycle_log
            save_state(state)
            return
        else:
            print(f"🎯 {kz_name} active — analyse des setups")

    # ─── Fear & Greed crypto : fetch 1× par cycle ─────────────────────────
    fg = None
    if SOCIAL_OK and MARKET == "crypto":
        try:
            fg = fetch_crypto_fear_greed()
            cycle_log["checks"]["fear_greed"] = {"value": fg.get("value"), "verdict": fg.get("verdict")}
            if fg.get("value"):
                emoji = {"EXTREME_FEAR":"💎","FEAR":"😰","NEUTRAL":"😐",
                         "GREED":"😎","EXTREME_GREED":"🤑"}.get(fg.get("verdict"), "?")
                print(f"{emoji} Crypto Fear & Greed : {fg['value']}/100 ({fg['label']})")
        except Exception as e:
            print(f"⚠️ Fear & Greed : {e}")

    # ─── DXY Confluence : fetch 1× par cycle pour or + forex ─────────────
    dxy = "NEUTRAL"
    yields = "NEUTRAL"
    if TM_OK and MARKET in ("forex", "gold"):
        dxy = fetch_dxy_trend()
        cycle_log["checks"]["dxy"] = {"trend": dxy}
        if dxy != "NEUTRAL":
            usd_state = "fort 💪" if dxy == "BULLISH" else "faible 📉"
            print(f"💵 DXY tendance : {dxy} (USD {usd_state})")

        # ─── US 10Y Yields : 1× par cycle ─────────────────────────────
        yields = fetch_10y_yield_trend()
        cycle_log["checks"]["yields_10y"] = {"trend": yields}
        if yields != "NEUTRAL":
            arrow = "📈 ↑" if yields == "BULLISH" else "📉 ↓"
            print(f"🏦 US 10Y Yields : {yields} {arrow}")

    for sym in SYMBOLS:
        nice = SYMBOLS_NICE.get(sym, sym)
        sym_log = {"symbol": nice, "decision": "?", "details": []}
        if sym in positions:
            sym_log["decision"] = "POSITION_OPEN"
            sym_log["details"].append(f"Position {positions[sym]['direction']} en cours")
            cycle_log["symbols"].append(sym_log)
            continue
        if len(positions) >= MAX_POSITIONS:
            sym_log["decision"] = "MAX_POSITIONS"
            cycle_log["symbols"].append(sym_log)
            continue

        # Filtre news
        try:
            allowed, reason = can_open_position(sym)
            if not allowed:
                print(f"🛡️ {nice} bloqué par news : {reason}")
                sym_log["decision"] = "BLOCKED_NEWS"
                sym_log["details"].append(reason or "")
                cycle_log["symbols"].append(sym_log)
                continue
        except Exception as e:
            print(f"⚠️ News filter {sym}: {e}")

        try:
            c_h4 = fetch_candles(sym, "H4")
            c_h1 = fetch_candles(sym, "H1")
            c_m5 = fetch_candles(sym, "M5")

            # FEATURE 3 : tendance Daily (D1)
            d1_trend = "NEUTRAL"
            if TM_OK and DATA_SOURCE == "yahoo":
                try:
                    c_d1 = fetch_yahoo_d1(sym, 100)
                    d1_trend = daily_bias(c_d1)
                except Exception as _e:
                    print(f"  ↪ D1 fetch KO: {_e}")
            elif TM_OK and DATA_SOURCE == "kraken":
                try:
                    c_d1 = fetch_kraken(sym, 1440, 100)  # 1440 min = 1d Kraken
                    d1_trend = daily_bias(c_d1)
                except Exception as _e:
                    print(f"  ↪ D1 fetch KO: {_e}")

            s_h4 = market_structure(c_h4)
            s_h1 = market_structure(c_h1, n=12)
            mss  = check_mss(c_m5)
            ob_bull, ob_bear = find_ob(c_h1)

            sym_log["d1"] = d1_trend
            sym_log["h4"] = s_h4["trend"]
            sym_log["h1"] = s_h1["trend"]
            sym_log["mss"] = mss

            if s_h4["trend"] == "BULLISH" and s_h1["trend"] == "BULLISH":
                bias = "LONG"
            elif s_h4["trend"] == "BEARISH" and s_h1["trend"] == "BEARISH":
                bias = "SHORT"
            elif MARKET in ("gold", "forex") and d1_trend != "NEUTRAL":
                # Or & Forex : assouplissement — D1 fort + au moins 1 TF aligné suffit.
                # Pourquoi ? H4 et H1 sont rarement parfaitement alignés simultanément.
                # Mais si la tendance Daily est claire, un seul TF intermédiaire suffit
                # comme confirmation. La D1 reste le filtre fort.
                emoji = "💛" if MARKET == "gold" else "💱"
                if d1_trend == "BULLISH" and (s_h4["trend"] == "BULLISH" or s_h1["trend"] == "BULLISH"):
                    bias = "LONG"
                    print(f"  {emoji} {MARKET} assoupli : D1={d1_trend} + (H4={s_h4['trend']} | H1={s_h1['trend']}) → LONG")
                elif d1_trend == "BEARISH" and (s_h4["trend"] == "BEARISH" or s_h1["trend"] == "BEARISH"):
                    bias = "SHORT"
                    print(f"  {emoji} {MARKET} assoupli : D1={d1_trend} + (H4={s_h4['trend']} | H1={s_h1['trend']}) → SHORT")
                else:
                    print(f"⏸ {nice} | H4:{s_h4['trend']} H1:{s_h1['trend']} D1:{d1_trend} → WAIT")
                    sym_log["decision"] = "TREND_NOT_ALIGNED"
                    sym_log["details"].append(f"H4={s_h4['trend']} H1={s_h1['trend']} D1={d1_trend}")
                    cycle_log["symbols"].append(sym_log)
                    continue
            else:
                print(f"⏸ {nice} | H4:{s_h4['trend']} H1:{s_h1['trend']} → WAIT")
                sym_log["decision"] = "TREND_NOT_ALIGNED"
                sym_log["details"].append(f"H4={s_h4['trend']} H1={s_h1['trend']}")
                cycle_log["symbols"].append(sym_log)
                continue

            sym_log["bias"] = bias

            # FEATURE 3 (bis) : refuser si pas aligné avec la tendance D1
            if TM_OK and not aligned_with_daily(bias, d1_trend):
                print(f"🚫 {nice} | {bias} contre tendance D1 ({d1_trend}) → SKIP")
                sym_log["decision"] = "AGAINST_D1"
                sym_log["details"].append(f"{bias} contre D1={d1_trend}")
                cycle_log["symbols"].append(sym_log)
                continue
            if d1_trend != "NEUTRAL":
                print(f"  ✓ Aligné avec D1 ({d1_trend})")

            expected_mss = "BULLISH_MSS" if bias == "LONG" else "BEARISH_MSS"
            if mss != expected_mss:
                print(f"⏳ {nice} | Biais:{bias} | MSS attendu:{expected_mss} actuel:{mss}")
                sym_log["decision"] = "WAIT_MSS"
                sym_log["details"].append(f"Attendu {expected_mss}, actuel {mss}")
                cycle_log["symbols"].append(sym_log)
                continue

            # Filtre corrélation
            if TM_OK:
                conflict, other = has_correlated_position(sym, bias, positions)
                if conflict:
                    print(f"🔗 {nice} bloqué : déjà {bias} sur {SYMBOLS_NICE.get(other, other)} (corrélé)")
                    sym_log["decision"] = "BLOCKED_CORRELATION"
                    sym_log["details"].append(f"Déjà {bias} sur {SYMBOLS_NICE.get(other, other)}")
                    cycle_log["symbols"].append(sym_log)
                    continue

            # Filtre DXY (or + forex uniquement)
            if TM_OK and MARKET in ("forex", "gold"):
                ok_dxy, why_dxy = dxy_aligned(sym, bias, dxy)
                if not ok_dxy:
                    print(f"💵 {nice} bloqué : {why_dxy}")
                    sym_log["decision"] = "BLOCKED_DXY"
                    sym_log["details"].append(why_dxy)
                    cycle_log["symbols"].append(sym_log)
                    continue
                if dxy != "NEUTRAL":
                    print(f"  ✓ DXY confirme : {why_dxy}")

                # Filtre US 10Y Yields — mode SOFT par défaut
                # Si yields contredisent : on laisse passer mais on réduit la mise -40%
                ok_y, why_y, yields_factor = yields_aligned(sym, bias, yields, mode="soft")
                if not ok_y:
                    # En mode hard ce serait un blocage, en soft ça n'arrive jamais sauf paire non-supportée
                    print(f"🏦 {nice} bloqué : {why_y}")
                    sym_log["decision"] = "BLOCKED_YIELDS"
                    sym_log["details"].append(why_y)
                    cycle_log["symbols"].append(sym_log)
                    continue
                if yields_factor < 1.0:
                    print(f"  ⚠️ {why_y}")
                    sym_log.setdefault("warnings",[]).append(why_y)
                elif yields != "NEUTRAL":
                    print(f"  ✓ Yields confirment : {why_y}")

            # Filtre COT (sentiment institutionnel — futures CFTC)
            if TM_OK:
                cot_trend, cot_detail = fetch_cot_sentiment(sym)
                sym_log["cot"] = cot_trend
                ok_cot, why_cot = cot_aligned(bias, cot_trend)
                if not ok_cot:
                    print(f"🏛️ {nice} bloqué : {why_cot}")
                    sym_log["decision"] = "BLOCKED_COT"
                    sym_log["details"].append(why_cot)
                    cycle_log["symbols"].append(sym_log)
                    continue
                if cot_trend != "NEUTRAL" and "net_pct" in cot_detail:
                    print(f"  ✓ COT confirme : pros {cot_trend} ({cot_detail['net_pct']:+.1f}% net)")

            # ATR calculé ICI — utilisé pour le SL de l'or + l'ajustement risque
            atr_now = None
            avg_atr = None
            if TM_OK:
                atr_now = compute_atr(c_h1, period=14)
                avg_atr = compute_atr(c_h4, period=14) if len(c_h4) > 14 else atr_now

            # ADX H4 : skip si le marché est en RANGE (la méthode ICT marche pas)
            if TM_OK:
                adx_val = compute_adx(c_h4, period=14)
                regime = market_regime(adx_val)
                sym_log["adx"] = adx_val
                sym_log["regime"] = regime
                if regime == "RANGE":
                    print(f"📉 {nice} bloqué : marché en range (ADX={adx_val})")
                    sym_log["decision"] = "BLOCKED_RANGE"
                    sym_log["details"].append(f"ADX={adx_val} (marché plat)")
                    cycle_log["symbols"].append(sym_log)
                    continue
                if adx_val:
                    print(f"  📊 ADX H4 = {adx_val} ({regime})")

            plan = build_plan(bias, s_h4, ob_bull, ob_bear, atr_h1=atr_now)
            print(f"📋 {nice} | {bias} | Entrée:{plan['entry']:.4f} | SL:{plan['sl']:.4f} | R:R:{plan['rr1']}")
            sym_log["rr"] = plan["rr1"]
            if not plan["valid"]:
                print(f"❌ {nice} R:R {plan['rr1']} < {MIN_RR}")
                sym_log["decision"] = "RR_TOO_LOW"
                sym_log["details"].append(f"R:R {plan['rr1']} < {MIN_RR}")
                cycle_log["symbols"].append(sym_log)
                continue

            # Risque ajusté par ATR (volatilité)
            base_risk = round(capital * RISK_PCT, 2)
            risk_amount = base_risk
            if TM_OK and atr_now:
                risk_amount = adjust_risk_by_atr(base_risk, atr_now, avg_atr)
                if risk_amount != base_risk:
                    print(f"📏 ATR ajustement : risque {base_risk}$ → {risk_amount}$")

            # Application du yields_factor (mode soft : -40% si yields contredisent)
            if MARKET in ("forex","gold") and 'yields_factor' in dir():
                try:
                    if yields_factor < 1.0:
                        before = risk_amount
                        risk_amount = round(risk_amount * yields_factor, 2)
                        print(f"   📉 Mise réduite par yields-soft : {before}€ → {risk_amount}€")
                except NameError: pass

            # Application Fear & Greed crypto (signal contrarien)
            if MARKET == "crypto" and SOCIAL_OK and fg and fg.get("value"):
                ok_fg, msg_fg, fg_factor = fear_greed_signal_for_crypto(fg, plan["direction"])
                if fg_factor != 1.0:
                    before = risk_amount
                    risk_amount = round(risk_amount * fg_factor, 2)
                    arrow = "↑ +" if fg_factor > 1 else "↓ -"
                    print(f"   {arrow}{abs(fg_factor-1)*100:.0f}% Fear&Greed : {before}€ → {risk_amount}€")
                    print(f"      → {msg_fg}")
                    sym_log.setdefault("fg_factor", fg_factor)

            # Conditions réelles : applique spread + slippage à l'entrée
            quoted_entry = plan["entry"]
            if REAL_COSTS_OK and REAL_COSTS_ENABLED:
                real_entry = apply_realistic_entry(sym, plan["direction"], quoted_entry)
                if real_entry != quoted_entry:
                    diff = abs(real_entry - quoted_entry)
                    print(f"   💱 Prix réel (spread+slippage) : {quoted_entry:.5f} → {real_entry:.5f} (Δ {diff:.5f})")
                plan["entry"] = real_entry

            capital -= risk_amount

            # Snapshot pour le dashboard : 30 dernières bougies + validations
            chart_candles = [
                {"o": round(c["open"], 5),  "h": round(c["high"], 5),
                 "l": round(c["low"], 5),   "c": round(c["close"], 5),
                 "ts": c["ts"] if isinstance(c["ts"], (int, float)) else str(c["ts"])}
                for c in c_h1[-30:]
            ]
            validations = [
                {"name": "Tendance Daily (D1)",   "ok": True, "detail": d1_trend},
                {"name": "Tendance H4",           "ok": True, "detail": s_h4["trend"]},
                {"name": "Tendance H1",           "ok": True, "detail": s_h1["trend"]},
                {"name": "Market Structure Shift", "ok": True, "detail": expected_mss},
                {"name": "Order Block trouvé",    "ok": True, "detail": "OB détecté sur H1"},
                {"name": "R:R minimum",           "ok": True, "detail": f"{plan['rr1']} ≥ {MIN_RR}"},
            ]
            if MARKET in ("forex", "gold"):
                validations.append({"name": "DXY (Dollar Index)", "ok": True,
                                    "detail": f"{dxy} compatible"})
                validations.append({"name": "US 10Y Yields",       "ok": True,
                                    "detail": f"{yields} compatible"})
            try:
                cot_trend_log, cot_dt = fetch_cot_sentiment(sym) if TM_OK else ("NEUTRAL", {})
                if cot_trend_log != "NEUTRAL":
                    validations.append({"name": "COT institutionnel", "ok": True,
                                        "detail": f"Pros {cot_trend_log} ({cot_dt.get('net_pct',0):+.1f}% net)"})
            except Exception: pass
            if TM_OK and adx_val:
                validations.append({"name": "Régime marché (ADX)", "ok": True,
                                    "detail": f"{regime} ADX={adx_val}"})

            positions[sym] = {
                "direction":            plan["direction"],
                "entry":                plan["entry"],
                "sl":                   plan["sl"],
                "initial_sl":           plan["sl"],          # garde l'origine pour calcul PnL
                "tp1":                  plan["tp1"],
                "rr1":                  plan["rr1"],
                "risk_amount":          risk_amount,
                "initial_risk_amount":  risk_amount,
                "be_set":               False,
                "tp1_taken":            False,
                "trail_active":         False,
                "open_time":            datetime.now(timezone.utc).isoformat(),
                "chart_candles":        chart_candles,
                "validations":          validations,
            }
            direction_fr = "va monter 📈" if bias == "LONG" else "va baisser 📉"
            notify(f"🎯 Nouveau pari sur {nice}",
                   f"{nice} {direction_fr}\nMise : {risk_amount:.0f}$\nGain potentiel : +{round(risk_amount * plan['rr1']):.0f}$",
                   priority=5, tags=["rocket" if bias == "LONG" else "chart_with_downwards_trend"])

            # ─── Mirror sur IG Markets (optionnel, DRY_RUN par défaut) ───
            if IG_OK and IG_ENABLED:
                ig_epic = bot_symbol_to_epic(sym)
                if ig_epic:
                    try:
                        with IGBroker(environment=IG_ENV, dry_run=IG_DRY_RUN) as ig:
                            ig_dir = "BUY" if bias == "LONG" else "SELL"
                            # Calcul approximatif des distances en pips
                            entry_p = plan["entry"]
                            sl_dist = abs(entry_p - plan["sl"])
                            tp_dist = abs(plan["tp1"] - entry_p)
                            # Conversion en pips selon l'instrument (forex : 0.0001 = 1 pip)
                            pip_factor = 10000 if "USD=X" in sym else 1
                            sl_pips = round(sl_dist * pip_factor, 1)
                            tp_pips = round(tp_dist * pip_factor, 1)
                            ig_result = ig.open_position(
                                epic=ig_epic, direction=ig_dir, size=1,
                                stop_distance=sl_pips, limit_distance=tp_pips,
                            )
                            mode = "DRY_RUN simulé" if IG_DRY_RUN else "ENVOYÉ EN RÉEL"
                            print(f"   🔌 IG {mode} : {ig_result}")
                            sym_log["ig_mirror"] = {"mode": mode, "epic": ig_epic}
                    except Exception as e:
                        print(f"   ⚠️ IG mirror KO : {e}")
            print(f"✅ POSITION OUVERTE {nice} {bias} | Risque: {risk_amount:.2f}$")
            sym_log["decision"] = "POSITION_OPENED"
            sym_log["details"].append(f"{bias} @ {plan['entry']:.4f} | risque {risk_amount}$")
            cycle_log["symbols"].append(sym_log)
            cycle_log["actions"].append(f"OUVERT {bias} {nice}")
        except Exception as e:
            print(f"⚠️ Erreur analyse {sym}: {e}")
            sym_log["decision"] = "ERROR"
            sym_log["details"].append(str(e)[:60])
            cycle_log["symbols"].append(sym_log)

    # ─── 3. SAUVEGARDER ──────────────────────────────────────────────────
    if "status" not in cycle_log:
        cycle_log["status"] = "ANALYZED"
    state["capital"]    = capital
    state["positions"]  = positions
    state["trades"]     = trades
    state["last_cycle"] = cycle_log
    save_state(state)

    wins = [t for t in trades if t["pnl"] > 0]
    wr   = round(len(wins) / len(trades) * 100, 1) if trades else 0
    pnl  = round(capital - CAPITAL_START, 2)
    print(f"✅ [{LABEL}] State sauve | Trades:{len(trades)} | WR:{wr}% | PnL:{pnl:+.2f}$ | Capital:{capital:.2f}$")

if __name__ == "__main__":
    run()
