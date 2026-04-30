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
    - forex/gold : Londres (7-17h UTC) + NY (12-21h UTC) = 7h à 21h UTC
    - crypto     : 24/7 (mais évite 0-6h UTC weekend, faible liquidité)
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
