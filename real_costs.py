#!/usr/bin/env python3
"""
Conditions réelles de trading — simulation des frais d'un broker pro (IG/Saxo).

Quand on passe d'un paper trading "parfait" à du réel, on perd 10-20% de PnL
à cause de :

1. SPREAD       : différence entre prix d'achat (offer) et prix de vente (bid).
                 Le broker prend cette marge à chaque entrée + sortie.
2. SLIPPAGE     : ton ordre est rempli légèrement plus haut/bas que prévu
                 quand le marché bouge vite (volatilité, news).
3. COMMISSION   : frais fixes par lot. Chez IG CFD = 0€ (compris dans spread).
                 Chez Interactive Brokers = ~0.20$ / lot mini.
4. SWAP         : intérêts overnight (positions tenues > 1 jour). Petit mais réel.

Usage dans le bot :
    from real_costs import apply_realistic_entry, apply_realistic_exit

    real_entry = apply_realistic_entry("EURUSD=X", "LONG", quoted_price)
    # → quoted_price + spread/2 + slippage random
"""

import random
from datetime import datetime, timezone

# ── SPREADS typiques chez IG France (en pips) ────────────────────────────
# Source : https://www.ig.com/fr/forex/spreads-forex
SPREADS = {
    "EURUSD=X":  0.6,    # 0.6 pip
    "GBPUSD=X":  0.9,
    "USDJPY=X":  0.7,
    "EURJPY=X":  1.5,
    "GBPJPY=X":  2.5,
    "GC=F":      0.3,    # 0.3 points sur l'or
    "XAUUSD=X":  0.3,
    # Crypto (les spreads crypto sont en valeur absolue, pas en pips)
    "XBTUSD":    "crypto_50",   # ~50$ de spread sur BTC
    "ETHUSD":    "crypto_5",    # ~5$ sur ETH
    "SOLUSD":    "crypto_0.5",
}

# Pip factor (combien vaut 1 pip)
PIP_FACTOR = {
    "EURUSD=X": 0.0001,
    "GBPUSD=X": 0.0001,
    "USDJPY=X": 0.01,
    "EURJPY=X": 0.01,
    "GBPJPY=X": 0.01,
    "GC=F":     0.01,
    "XAUUSD=X": 0.01,
}

# Slippage typique (en fraction de pip — 0.2 = 0.2 pip)
SLIPPAGE_RANGE = (0.0, 0.4)  # entre 0 et 0.4 pip, random uniforme

# Frais commissions (CFD chez IG France = 0)
COMMISSION_PER_TRADE = 0.0   # en € par trade

# Frais swap overnight (par jour de détention, en % de la position)
SWAP_RATE_DAILY = {
    "default": 0.0001,   # 0.01%/jour ≈ 3.6%/an
}


def get_spread_pips(symbol):
    """Renvoie le spread en pips pour un symbole."""
    s = SPREADS.get(symbol, 1.0)
    if isinstance(s, str): return None  # crypto, pas en pips
    return s

def get_spread_value(symbol, mid_price):
    """Renvoie le spread en valeur absolue (€/$)."""
    s = SPREADS.get(symbol, 1.0)
    if isinstance(s, str):
        # Crypto : valeur fixe
        return float(s.split("_")[1])
    pip = PIP_FACTOR.get(symbol, 0.0001)
    return s * pip

def apply_realistic_entry(symbol, direction, quoted_price):
    """
    Ajuste le prix d'entrée pour simuler les conditions réelles :
    - LONG : tu paies l'OFFER (prix le plus haut) = quoted + spread/2 + slippage
    - SHORT : tu paies le BID (prix le plus bas)  = quoted - spread/2 - slippage

    Renvoie le prix réel d'exécution (toujours pire que le quoted_price).
    """
    spread = get_spread_value(symbol, quoted_price)
    pip = PIP_FACTOR.get(symbol, 0.0001)
    slippage_pips = random.uniform(*SLIPPAGE_RANGE)
    slippage_val = slippage_pips * pip if pip else 0

    # Pour crypto sans pip
    if isinstance(SPREADS.get(symbol), str):
        # Slippage crypto = 0.05% du prix
        slippage_val = quoted_price * 0.0005 * random.random()

    cost = (spread / 2) + slippage_val
    if direction.upper() == "LONG":
        return round(quoted_price + cost, 6)
    else:
        return round(quoted_price - cost, 6)

def apply_realistic_exit(symbol, direction, quoted_price):
    """
    À la sortie c'est l'inverse :
    - On ferme un LONG → on vend au BID (subit -spread/2 -slippage)
    - On ferme un SHORT → on rachète à l'OFFER (subit +spread/2 +slippage)
    """
    spread = get_spread_value(symbol, quoted_price)
    pip = PIP_FACTOR.get(symbol, 0.0001)
    slippage_pips = random.uniform(*SLIPPAGE_RANGE)
    slippage_val = slippage_pips * pip if pip else 0
    if isinstance(SPREADS.get(symbol), str):
        slippage_val = quoted_price * 0.0005 * random.random()
    cost = (spread / 2) + slippage_val
    if direction.upper() == "LONG":
        return round(quoted_price - cost, 6)
    else:
        return round(quoted_price + cost, 6)

def compute_swap_fees(symbol, position_value, days_held):
    """Calcule les frais swap pour une position tenue X jours."""
    rate = SWAP_RATE_DAILY.get(symbol, SWAP_RATE_DAILY["default"])
    return round(position_value * rate * days_held, 2)


# ── CLI test ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Conditions réelles de trading — simulation des frais\n")
    cases = [
        ("EURUSD=X", "LONG",  1.1700),
        ("EURUSD=X", "SHORT", 1.1700),
        ("GBPUSD=X", "LONG",  1.3500),
        ("GC=F",     "LONG",  4600.00),
        ("XBTUSD",   "LONG",  78000),
    ]
    for sym, direction, price in cases:
        entry = apply_realistic_entry(sym, direction, price)
        exit_p = apply_realistic_exit(sym, direction, price)
        cost = abs(entry - price) + abs(exit_p - price)
        spread = get_spread_value(sym, price)
        print(f"📊 {sym} {direction} à {price}")
        print(f"   Spread broker  : {spread}")
        print(f"   Prix d'entrée  : {entry} (vs quoted {price})")
        print(f"   Prix de sortie : {exit_p}")
        print(f"   Coût total ↔   : {cost:.5f}")
        print()
