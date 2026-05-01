#!/usr/bin/env python3
"""
Trade Manager — gestion pro des positions :

1. BREAKEVEN          : SL → entrée quand 50% du chemin vers TP est fait
2. TP PARTIEL         : 50% fermé au TP1, 50% laissé en runner
3. TRAILING STOP      : après TP1, le SL suit le prix
4. ATR (volatilité)   : taille de position adaptée au marché
5. SESSIONS           : trade seulement Londres + NY
6. CORRÉLATION        : évite EUR/USD + GBP/USD en même direction
"""

import json, os, urllib.request, urllib.parse
from datetime import datetime, timezone

# ── 1) BREAKEVEN ──────────────────────────────────────────────────────────
def maybe_set_breakeven(pos, current_price):
    """
    Si le prix a parcouru 50% du chemin vers TP, on déplace le SL à l'entrée.
    Position devient sans risque ('pari gratuit').
    Renvoie (modified, message_ou_None).
    """
    if pos.get("be_set"):
        return False, None  # déjà fait

    entry, tp = pos["entry"], pos["tp1"]
    half = entry + (tp - entry) * 0.5  # mi-chemin entrée→TP

    triggered = (
        (pos["direction"] == "LONG"  and current_price >= half) or
        (pos["direction"] == "SHORT" and current_price <= half)
    )
    if not triggered:
        return False, None

    pos["sl"]      = entry
    pos["be_set"]  = True
    return True, "Breakeven activé : SL placé à l'entrée → ne peux plus perdre"

# ── 2 + 3) TP PARTIEL + TRAILING STOP ─────────────────────────────────────
def maybe_take_partial(pos, current_price):
    """
    Quand le TP1 est touché : on prend 50% du gain et on laisse courir 50%.
    Active le trailing stop sur le reste.
    Renvoie (partial_pnl_to_add_to_capital_or_None, message).
    """
    if pos.get("tp1_taken"):
        return None, None

    triggered = (
        (pos["direction"] == "LONG"  and current_price >= pos["tp1"]) or
        (pos["direction"] == "SHORT" and current_price <= pos["tp1"])
    )
    if not triggered:
        return None, None

    # On empoche 50% du gain prévu
    half_pnl = round(pos["risk_amount"] * pos["rr1"] * 0.5, 2)

    # On garde 50% de la mise pour la suite
    pos["risk_amount"] = round(pos["risk_amount"] * 0.5, 2)

    # Active le trailing stop : SL initial = entrée (breakeven)
    pos["sl"]            = pos["entry"]
    pos["tp1_taken"]     = True
    pos["trail_active"]  = True
    pos["trail_high"]    = current_price  # plus haut atteint depuis activation

    # On étend le TP de 50% pour laisser courir
    pos["tp1"] = pos["tp1"] + (pos["tp1"] - pos["entry"]) * 0.5

    return half_pnl, f"50% sécurisé (+{half_pnl:.0f}$) — l'autre moitié continue avec trailing stop"

def update_trailing_stop(pos, current_price, distance_pct=0.5):
    """
    Si trailing actif : SL suit le prix à 'distance_pct' du chemin parcouru.
    Plus simple : SL = max ancien SL, prix - 50% de la distance prix-entrée.
    Renvoie (modified, new_sl).
    """
    if not pos.get("trail_active"):
        return False, None

    if pos["direction"] == "LONG":
        # Distance = (prix actuel - entrée) * (1 - distance_pct)
        # SL ne peut que MONTER (jamais redescendre)
        new_sl = pos["entry"] + (current_price - pos["entry"]) * distance_pct
        if new_sl > pos["sl"]:
            pos["sl"] = new_sl
            return True, new_sl
    else:  # SHORT
        new_sl = pos["entry"] - (pos["entry"] - current_price) * distance_pct
        if new_sl < pos["sl"]:
            pos["sl"] = new_sl
            return True, new_sl
    return False, None

# ── 4) ATR (volatilité) ───────────────────────────────────────────────────
def compute_atr(candles, period=14):
    """
    ATR = volatilité moyenne sur N bougies.
    Plus c'est haut, plus le marché bouge fort.
    """
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        prev, curr = candles[i-1], candles[i]
        tr = max(
            curr["high"] - curr["low"],
            abs(curr["high"] - prev["close"]),
            abs(curr["low"]  - prev["close"]),
        )
        trs.append(tr)
    return sum(trs[-period:]) / period

def adjust_risk_by_atr(base_risk, atr_value, avg_atr):
    """
    Ajuste la mise selon la volatilité du marché.
    - Marché calme (ATR < moyenne) → mise *normale*
    - Marché agité (ATR > moyenne) → mise réduite (moins de chance d'erreur)
    """
    if not atr_value or not avg_atr or avg_atr == 0:
        return base_risk
    ratio = atr_value / avg_atr
    if ratio > 1.5:    return round(base_risk * 0.5, 2)  # très agité → -50%
    if ratio > 1.2:    return round(base_risk * 0.75, 2) # agité → -25%
    return base_risk

# ── 5) FILTRE SESSIONS ────────────────────────────────────────────────────
def in_active_session(market="forex"):
    """
    Trade que pendant les heures où la liquidité est bonne.
    - gold   : 2h-21h UTC (inclut session Asie : Shanghai Gold Exchange très actif)
    - forex  : 7h-21h UTC (Londres + NY uniquement)
    - crypto : 24/7 (mais évite 0-6h UTC weekend, faible liquidité)
    """
    now = datetime.now(timezone.utc)
    h, wd = now.hour, now.weekday()  # weekday: 0=lundi, 6=dimanche

    if market == "crypto":
        # Crypto trade 24/7 mais évite les nuits weekend (samedi+dimanche, 0-6h UTC)
        if wd in (5, 6) and h < 6:
            return False, "Nuit weekend crypto (faible liquidité)"
        return True, None

    # forex / gold : marché fermé weekend
    if wd >= 5:  # samedi ou dimanche
        return False, "Weekend (Forex/Or fermé)"

    # Or : session élargie (Asie/Shanghai actif dès 2h UTC)
    if market == "gold":
        if h < 2 or h >= 21:
            return False, f"Hors session Or ({h}h UTC, attendre 2h-21h UTC)"
        return True, None

    # Forex : sessions classiques London + NY
    if h < 7 or h >= 21:
        return False, f"Hors session active ({h}h UTC, attendre 7h-21h UTC)"
    return True, None

# ── 6) FILTRE CORRÉLATION ─────────────────────────────────────────────────
CORRELATED_PAIRS = {
    "EURUSD=X": ["GBPUSD=X"],   # corrélation ~80%
    "GBPUSD=X": ["EURUSD=X"],
    "XBTUSD":   ["ETHUSD"],     # BTC et ETH bougent ensemble
    "ETHUSD":   ["XBTUSD", "SOLUSD"],
    "SOLUSD":   ["ETHUSD"],
}

def has_correlated_position(symbol, direction, current_positions):
    """
    Vérifie si on a déjà une position dans la même direction sur une paire corrélée.
    Renvoie (True, autre_symbole) si conflit, sinon (False, None).
    """
    for corr_sym in CORRELATED_PAIRS.get(symbol, []):
        if corr_sym in current_positions:
            if current_positions[corr_sym]["direction"] == direction:
                return True, corr_sym
    return False, None

# ── 7) LIMITE DE PERTE QUOTIDIENNE ────────────────────────────────────────
DAILY_LOSS_LIMIT_PCT = 0.03  # -3% par jour max

def daily_loss_exceeded(state, capital_start=1000.0):
    """
    Calcule la perte du jour (depuis 00h UTC). Renvoie (True, perte_pct) si > 3%.
    Empêche d'ouvrir de nouveaux trades pour le reste de la journée.
    """
    trades = state.get("trades", [])
    today = datetime.now(timezone.utc).date().isoformat()
    today_pnl = sum(
        t["pnl"] for t in trades
        if t.get("time", "").startswith(today)
    )
    today_pnl_pct = today_pnl / capital_start
    if today_pnl_pct <= -DAILY_LOSS_LIMIT_PCT:
        return True, round(today_pnl_pct * 100, 2)
    return False, round(today_pnl_pct * 100, 2)

# ── 8) KILLZONES ICT (heures où les institutions tradent) ─────────────────
def in_killzone(market="forex"):
    """
    Killzones ICT = heures où 80% des mouvements rentables se produisent :
    - Londres Open : 7h-10h UTC
    - NY Open      : 12h-15h UTC
    - NY PM        : 18h-20h UTC

    Or (gold) : killzone Asie en plus (Shanghai Gold Exchange + BIS flows)
    - Asie Or    : 2h-6h UTC (peak liquidité Chine/Japon sur l'or)

    Crypto : killzone Asia aussi mais plus tôt
    Renvoie (True/False, nom_de_killzone).
    """
    now = datetime.now(timezone.utc)
    h = now.hour

    # Or : killzone Asie (Shanghai Gold Exchange — très actif sur l'or physique)
    if market == "gold" and 2 <= h < 6:
        return True, "Killzone Asie Or (Shanghai)"

    if 7 <= h < 10:    return True, "Killzone Londres Open"
    if 12 <= h < 15:   return True, "Killzone NY Open"

    # Forex : killzone NY mid-session étendue (15h-17h UTC)
    # Beaucoup de mouvements après les news US sortent à 14h30 UTC,
    # le retest et les vrais départs de tendance ont lieu 15h-17h.
    if market == "forex" and 15 <= h < 17:
        return True, "Killzone NY mid-session"

    if 18 <= h < 20:   return True, "Killzone NY PM"

    # Crypto a une killzone Asia en plus
    if market == "crypto" and 0 <= h < 3:
        return True, "Killzone Asia"

    return False, None

# ── 9) BIAS DAILY (D1) — ne trader que dans le sens de la grosse tendance ─
def daily_bias(d1_candles):
    """
    Détermine la tendance Daily : BULLISH / BEARISH / NEUTRAL.
    Basé sur EMA50 D1 + position du dernier close.
    """
    if not d1_candles or len(d1_candles) < 50:
        return "NEUTRAL"
    closes = [c["close"] for c in d1_candles]
    # EMA 50
    ema = closes[0]
    k = 2 / 51
    for c in closes[1:]:
        ema = c * k + ema * (1 - k)
    last = closes[-1]
    # Marge de 0.5% pour éviter les zones de range
    if last > ema * 1.005:  return "BULLISH"
    if last < ema * 0.995:  return "BEARISH"
    return "NEUTRAL"

def aligned_with_daily(direction, d1_bias):
    """Le trade est-il dans le sens de la tendance Daily ?"""
    if d1_bias == "NEUTRAL": return True  # neutre = OK les 2 sens
    return (direction == "LONG" and d1_bias == "BULLISH") or \
           (direction == "SHORT" and d1_bias == "BEARISH")

# ── 10) CONFLUENCE DXY (Dollar Index) ─────────────────────────────────────
# Pourquoi ? Le dollar est l'autre côté de chaque trade EUR/USD, GBP/USD, XAU/USD.
# Si on LONG EUR/USD, on parie sur USD qui baisse → vérifier que DXY baisse aussi.
# Si DXY monte alors qu'on veut LONG EUR/USD → signal contradictoire → on skip.

# Symboles affectés par le DXY (avec leur sens)
DXY_AFFECTED = {
    "EURUSD=X": "INVERSE",  # USD au dénominateur → DXY ↑ = EUR/USD ↓
    "GBPUSD=X": "INVERSE",
    "GC=F":     "INVERSE",  # Or côté en USD → DXY ↑ = Or ↓
    "XAUUSD=X": "INVERSE",
}

def fetch_dxy_trend(api_key=None):
    """
    Récupère la tendance DXY via TwelveData (D1, EMA50).
    Renvoie 'BULLISH' (USD fort), 'BEARISH' (USD faible), 'NEUTRAL'.
    """
    api_key = api_key or os.environ.get("TWELVE_API_KEY", "86757c28a7e3491ba6aa12f59aa13065")
    url = (f"https://api.twelvedata.com/time_series"
           f"?symbol=DXY&interval=1day&outputsize=100"
           f"&apikey={api_key}&timezone=UTC&order=ASC")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        if data.get("status") == "error": return "NEUTRAL"
        closes = [float(b["close"]) for b in data.get("values", []) if b.get("close")]
        if len(closes) < 50: return "NEUTRAL"
        return _trend_from_closes(closes)
    except Exception as e:
        print(f"  ↪ DXY fetch KO: {e}")
        return "NEUTRAL"

def _trend_from_closes(closes):
    """EMA50 + marge 0.3% (DXY est moins volatile que les paires forex)."""
    ema = closes[0]
    k = 2 / 51
    for c in closes[1:]:
        ema = c * k + ema * (1 - k)
    last = closes[-1]
    if last > ema * 1.003: return "BULLISH"   # USD fort
    if last < ema * 0.997: return "BEARISH"   # USD faible
    return "NEUTRAL"

def dxy_aligned(symbol, direction, dxy_trend):
    """
    Le trade est-il aligné avec le DXY ?
    Pour les paires INVERSE (EUR/USD, GBP/USD, Or) :
    - LONG  → on veut USD qui baisse → DXY != BULLISH
    - SHORT → on veut USD qui monte  → DXY != BEARISH
    Si DXY est NEUTRAL → on laisse passer (pas de contradiction).
    """
    if dxy_trend == "NEUTRAL": return True, "DXY neutre"
    rel = DXY_AFFECTED.get(symbol)
    if not rel: return True, "DXY non applicable"  # paires non-USD

    if rel == "INVERSE":
        if direction == "LONG"  and dxy_trend == "BULLISH":
            return False, f"DXY haussier (USD fort) bloque LONG {symbol}"
        if direction == "SHORT" and dxy_trend == "BEARISH":
            return False, f"DXY baissier (USD faible) bloque SHORT {symbol}"
    return True, f"DXY {dxy_trend} compatible"
