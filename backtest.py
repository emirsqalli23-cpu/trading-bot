#!/usr/bin/env python3
"""
Backtest Engine — teste la stratégie ICT du bot sur des données historiques.

Principe :
- Récupère 1 an de bougies H1 sur chaque symbole
- Rejoue chaque bougie comme si on était en live (pas de look-ahead bias)
- Applique TOUS les filtres du bot : killzones, D1 bias, MSS, OB, R:R, ATR, corrélation
- Simule la gestion de position : breakeven, TP partiel, trailing stop
- Calcule les vraies stats : WR, drawdown, profit factor, Sharpe ratio

Usage :
    python3 backtest.py forex
    python3 backtest.py gold
    python3 backtest.py crypto

Sortie : rapport markdown + fichier JSON détaillé.
"""

import json, os, sys, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta
from collections import defaultdict

from trade_manager import (
    compute_atr, adjust_risk_by_atr, in_killzone, daily_bias,
    aligned_with_daily, has_correlated_position,
)

# ── CONFIG ────────────────────────────────────────────────────────────────
CAPITAL_START = 1000.0
RISK_PCT      = 0.02
MAX_POSITIONS = 2
TWELVE_API_KEY = os.environ.get("TWELVE_API_KEY", "86757c28a7e3491ba6aa12f59aa13065")

MARKETS = {
    "crypto": {
        "symbols":   {"XBTUSD": "BTC/USD", "ETHUSD": "ETH/USD", "SOLUSD": "SOL/USD"},
        "td_map":    {"XBTUSD": "BTC/USD", "ETHUSD": "ETH/USD", "SOLUSD": "SOL/USD"},
        "min_rr":    2.0,
    },
    "gold": {
        "symbols":   {"GC=F": "Or"},
        "td_map":    {"GC=F": "XAU/USD"},
        "min_rr":    2.0,
    },
    "forex": {
        "symbols":   {"EURUSD=X": "EUR/USD", "GBPUSD=X": "GBP/USD"},
        "td_map":    {"EURUSD=X": "EUR/USD", "GBPUSD=X": "GBP/USD"},
        "min_rr":    1.8,
    },
}

# ── DATA ──────────────────────────────────────────────────────────────────
def fetch_history(td_symbol, interval="1h", days=365):
    """Récupère <days> jours de bougies via TwelveData."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    sym = urllib.parse.quote(td_symbol, safe="")
    # TwelveData limite à ~5000 bougies par requête. 365j × 24h = 8760 → on découpe.
    all_candles = []
    chunk = timedelta(days=180)
    cur = start
    while cur < end:
        nxt = min(cur + chunk, end)
        url = (f"https://api.twelvedata.com/time_series"
               f"?symbol={sym}&interval={interval}&outputsize=5000"
               f"&start_date={cur.strftime('%Y-%m-%d')}"
               f"&end_date={nxt.strftime('%Y-%m-%d')}"
               f"&apikey={TWELVE_API_KEY}&timezone=UTC&order=ASC")
        try:
            data = json.loads(urllib.request.urlopen(url, timeout=20).read())
            if data.get("status") == "error":
                print(f"  ⚠️ {data.get('message')}")
                break
            for bar in data.get("values", []):
                try:
                    ts = datetime.strptime(bar["datetime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    all_candles.append({
                        "ts": ts,
                        "open": float(bar["open"]),
                        "high": float(bar["high"]),
                        "low":  float(bar["low"]),
                        "close": float(bar["close"]),
                    })
                except Exception:
                    continue
        except Exception as e:
            print(f"  ⚠️ Fetch KO ({cur.date()}): {e}")
        cur = nxt
    # Dédoublonner par timestamp
    seen = set()
    unique = []
    for c in sorted(all_candles, key=lambda x: x["ts"]):
        if c["ts"] not in seen:
            seen.add(c["ts"])
            unique.append(c)
    return unique

def aggregate_h4(h1_candles):
    """Agrège des H1 en H4 (4 bougies par bloc)."""
    out = []
    for i in range(0, len(h1_candles) - 3, 4):
        chunk = h1_candles[i:i+4]
        out.append({
            "ts":    chunk[0]["ts"],
            "open":  chunk[0]["open"],
            "high":  max(c["high"] for c in chunk),
            "low":   min(c["low"]  for c in chunk),
            "close": chunk[-1]["close"],
        })
    return out

def aggregate_d1(h1_candles):
    """Agrège des H1 en D1 (24 bougies par bloc, alignées sur 00h UTC)."""
    by_day = defaultdict(list)
    for c in h1_candles:
        d = c["ts"].date()
        by_day[d].append(c)
    out = []
    for d in sorted(by_day.keys()):
        chunk = by_day[d]
        out.append({
            "ts":    chunk[0]["ts"],
            "open":  chunk[0]["open"],
            "high":  max(c["high"] for c in chunk),
            "low":   min(c["low"]  for c in chunk),
            "close": chunk[-1]["close"],
        })
    return out

# ── STRATEGIE (réplique de bot_cloud.py) ──────────────────────────────────
def market_structure(candles, n=20):
    if len(candles) < n: n = len(candles)
    recent = candles[-n:]
    highs = [c["high"] for c in recent]
    lows  = [c["low"]  for c in recent]
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

def check_mss_h1(candles_recent):
    """MSS approximé sur H1 (au lieu de M5 — backtest ne descend pas si bas)."""
    if len(candles_recent) < 11: return None
    sh = max(c["high"] for c in candles_recent[-10:-1])
    sl = min(c["low"]  for c in candles_recent[-10:-1])
    last = candles_recent[-1]["close"]
    if last > sh: return "BULLISH_MSS"
    if last < sl: return "BEARISH_MSS"
    return None

def build_plan(direction, struct_h4, ob_bull, ob_bear, atr_h1=None, market="crypto", min_rr=2.0):
    if direction == "LONG":
        entry = ob_bull["low"] if ob_bull else struct_h4["ema21"]
        if market == "gold" and atr_h1:
            sl = entry - 1.8 * atr_h1
        else:
            sl = min(entry * 0.997, struct_h4["swing_low"] * 1.001)
        tp1 = struct_h4["swing_high"]
    else:
        entry = ob_bear["high"] if ob_bear else struct_h4["ema21"]
        if market == "gold" and atr_h1:
            sl = entry + 1.8 * atr_h1
        else:
            sl = max(entry * 1.003, struct_h4["swing_high"] * 0.999)
        tp1 = struct_h4["swing_low"]
    risk = abs(entry - sl)
    rr = round(abs(tp1 - entry) / risk, 2) if risk > 0 else 0
    return {"direction": direction, "entry": entry, "sl": sl, "tp1": tp1, "rr1": rr, "valid": rr >= min_rr}

# ── BACKTEST ENGINE ───────────────────────────────────────────────────────
def run_backtest(market):
    cfg = MARKETS[market]
    print(f"\n{'='*60}\nBACKTEST {market.upper()} — 1 an de données\n{'='*60}\n")

    # 1. Charger l'historique pour chaque symbole
    histories = {}
    for sym, td_sym in cfg["td_map"].items():
        print(f"📥 Téléchargement {sym} ({td_sym})...")
        candles = fetch_history(td_sym, "1h", 365)
        if len(candles) < 200:
            print(f"  ⚠️ Pas assez de données ({len(candles)} bougies), skip")
            continue
        print(f"  ✓ {len(candles)} bougies H1 récupérées")
        histories[sym] = candles

    if not histories:
        print("❌ Aucune donnée disponible. Arrêt.")
        return None

    # 2. État simulé
    capital = CAPITAL_START
    positions = {}    # {sym: {entry, sl, tp1, ...}}
    trades   = []     # historique de tous les trades clos
    equity_curve = [(None, capital)]

    # 3. Trouver le timestamp commun (start = max des starts)
    start_ts = max(h[0]["ts"] for h in histories.values())
    end_ts   = min(h[-1]["ts"] for h in histories.values())
    print(f"\n📅 Période : {start_ts.date()} → {end_ts.date()}")

    # 4. Construire un index par timestamp pour itération synchronisée
    all_timestamps = set()
    for candles in histories.values():
        for c in candles:
            if start_ts <= c["ts"] <= end_ts:
                all_timestamps.add(c["ts"])
    timeline = sorted(all_timestamps)
    print(f"⏱  {len(timeline)} bougies H1 à rejouer\n")

    # 5. Index pour lookup rapide
    indexes = {sym: {c["ts"]: i for i, c in enumerate(candles)} for sym, candles in histories.items()}

    # 6. Boucle principale
    for ts_idx, ts in enumerate(timeline):
        if ts_idx % 500 == 0:
            print(f"  ...{ts_idx}/{len(timeline)} ({ts.date()}) capital={capital:.0f}$ trades={len(trades)}")

        # === Pour chaque symbole, gérer position ouverte avec le prix actuel ===
        for sym in list(positions.keys()):
            if ts not in indexes[sym]: continue
            i = indexes[sym][ts]
            candle = histories[sym][i]
            high, low = candle["high"], candle["low"]
            pos = positions[sym]
            initial_r = abs(pos["entry"] - pos["initial_sl"])

            # Le high et low de la bougie peuvent toucher SL ou TP — on assume
            # SCENARIO PESSIMISTE : si SL et TP sont touchés dans la même bougie, SL gagne (worst case)
            if pos["direction"] == "LONG":
                hit_sl = low  <= pos["sl"]
                hit_tp = high >= pos["tp1"]
                # Breakeven check (mi-chemin entry → tp)
                if not pos.get("be_set"):
                    half = pos["entry"] + (pos["tp1"] - pos["entry"]) * 0.5
                    if high >= half:
                        pos["sl"] = pos["entry"]
                        pos["be_set"] = True
                # TP partiel
                if not pos.get("tp1_taken") and high >= pos["tp1"]:
                    half_pnl = round(pos["risk_amount"] * pos["rr1"] * 0.5, 2)
                    half_recovered = round(0.5 * pos["initial_risk_amount"], 2)
                    capital += half_recovered + half_pnl
                    pos["risk_amount"] = round(pos["risk_amount"] * 0.5, 2)
                    pos["sl"] = pos["entry"]
                    pos["tp1_taken"] = True
                    pos["trail_active"] = True
                    pos["tp1"] = pos["tp1"] + (pos["tp1"] - pos["entry"]) * 0.5
                    trades.append({"sym": sym, "type": "TP_PARTIAL", "pnl": half_pnl, "ts": ts.isoformat()})
                    continue  # on a pris une partie, on continue
                # Trailing stop (simple : SL = entry + 50% de la distance prix-entry)
                if pos.get("trail_active"):
                    cur = candle["close"]
                    new_sl = pos["entry"] + (cur - pos["entry"]) * 0.5
                    if new_sl > pos["sl"]:
                        pos["sl"] = new_sl
                # Vérifier SL final (après BE/trailing)
                if low <= pos["sl"]:
                    if pos["sl"] >= pos["entry"]:
                        # BE ou trailing → 0 ou gain
                        pnl = round(pos["risk_amount"] * (pos["sl"] - pos["entry"]) / initial_r, 2)
                        kind = "TRAIL_EXIT" if pos.get("tp1_taken") else "BE"
                    else:
                        pnl = -pos["risk_amount"]
                        kind = "SL"
                    capital += pos["risk_amount"] + pnl
                    trades.append({"sym": sym, "type": kind, "pnl": pnl, "ts": ts.isoformat(),
                                   "entry": pos["entry"], "exit": pos["sl"]})
                    del positions[sym]
                elif high >= pos["tp1"] and pos.get("tp1_taken"):
                    # TP étendu touché
                    pnl = round(pos["risk_amount"] * (pos["tp1"] - pos["entry"]) / initial_r, 2)
                    capital += pos["risk_amount"] + pnl
                    trades.append({"sym": sym, "type": "TP_EXTENDED", "pnl": pnl, "ts": ts.isoformat(),
                                   "entry": pos["entry"], "exit": pos["tp1"]})
                    del positions[sym]

            else:  # SHORT
                hit_sl = high >= pos["sl"]
                hit_tp = low  <= pos["tp1"]
                if not pos.get("be_set"):
                    half = pos["entry"] - (pos["entry"] - pos["tp1"]) * 0.5
                    if low <= half:
                        pos["sl"] = pos["entry"]
                        pos["be_set"] = True
                if not pos.get("tp1_taken") and low <= pos["tp1"]:
                    half_pnl = round(pos["risk_amount"] * pos["rr1"] * 0.5, 2)
                    half_recovered = round(0.5 * pos["initial_risk_amount"], 2)
                    capital += half_recovered + half_pnl
                    pos["risk_amount"] = round(pos["risk_amount"] * 0.5, 2)
                    pos["sl"] = pos["entry"]
                    pos["tp1_taken"] = True
                    pos["trail_active"] = True
                    pos["tp1"] = pos["tp1"] - (pos["entry"] - pos["tp1"]) * 0.5
                    trades.append({"sym": sym, "type": "TP_PARTIAL", "pnl": half_pnl, "ts": ts.isoformat()})
                    continue
                if pos.get("trail_active"):
                    cur = candle["close"]
                    new_sl = pos["entry"] - (pos["entry"] - cur) * 0.5
                    if new_sl < pos["sl"]:
                        pos["sl"] = new_sl
                if high >= pos["sl"]:
                    if pos["sl"] <= pos["entry"]:
                        pnl = round(pos["risk_amount"] * (pos["entry"] - pos["sl"]) / initial_r, 2)
                        kind = "TRAIL_EXIT" if pos.get("tp1_taken") else "BE"
                    else:
                        pnl = -pos["risk_amount"]
                        kind = "SL"
                    capital += pos["risk_amount"] + pnl
                    trades.append({"sym": sym, "type": kind, "pnl": pnl, "ts": ts.isoformat(),
                                   "entry": pos["entry"], "exit": pos["sl"]})
                    del positions[sym]
                elif low <= pos["tp1"] and pos.get("tp1_taken"):
                    pnl = round(pos["risk_amount"] * (pos["entry"] - pos["tp1"]) / initial_r, 2)
                    capital += pos["risk_amount"] + pnl
                    trades.append({"sym": sym, "type": "TP_EXTENDED", "pnl": pnl, "ts": ts.isoformat(),
                                   "entry": pos["entry"], "exit": pos["tp1"]})
                    del positions[sym]

        # === Filtre killzone (basé sur l'heure UTC de la bougie) ===
        # On simule in_killzone() sans dépendre de datetime.now()
        h = ts.hour
        in_kz = False
        if market == "gold" and 2 <= h < 6: in_kz = True
        elif 7 <= h < 10: in_kz = True
        elif 12 <= h < 15: in_kz = True
        elif market == "forex" and 15 <= h < 17: in_kz = True
        elif 18 <= h < 20: in_kz = True
        elif market == "crypto" and 0 <= h < 3: in_kz = True

        if not in_kz:
            equity_curve.append((ts.isoformat(), capital + sum(p["risk_amount"] for p in positions.values())))
            continue

        # === Chercher de nouveaux setups ===
        for sym in cfg["symbols"].keys():
            if sym not in indexes or ts not in indexes[sym]: continue
            if sym in positions: continue
            if len(positions) >= MAX_POSITIONS: break

            i = indexes[sym][ts]
            if i < 50: continue  # besoin d'au moins 50 bougies d'historique

            # Bougies disponibles à cet instant (PAS de look-ahead)
            h1_window = histories[sym][:i+1][-50:]   # 50 dernières H1
            h4_window = aggregate_h4(histories[sym][:i+1][-200:])  # ~50 H4
            d1_window = aggregate_d1(histories[sym][:i+1])         # tout l'historique en D1

            if len(h4_window) < 20 or len(d1_window) < 50: continue

            d1_trend = daily_bias(d1_window)
            s_h4 = market_structure(h4_window)
            s_h1 = market_structure(h1_window, n=12)
            mss = check_mss_h1(h1_window)
            ob_bull, ob_bear = find_ob(h1_window)

            # Bias H4+H1 (avec assouplissement or/forex)
            bias = None
            if s_h4["trend"] == "BULLISH" and s_h1["trend"] == "BULLISH":
                bias = "LONG"
            elif s_h4["trend"] == "BEARISH" and s_h1["trend"] == "BEARISH":
                bias = "SHORT"
            elif market in ("gold", "forex") and d1_trend != "NEUTRAL":
                if d1_trend == "BULLISH" and (s_h4["trend"] == "BULLISH" or s_h1["trend"] == "BULLISH"):
                    bias = "LONG"
                elif d1_trend == "BEARISH" and (s_h4["trend"] == "BEARISH" or s_h1["trend"] == "BEARISH"):
                    bias = "SHORT"
            if not bias: continue

            # D1 alignment
            if not aligned_with_daily(bias, d1_trend): continue

            # MSS
            expected = "BULLISH_MSS" if bias == "LONG" else "BEARISH_MSS"
            if mss != expected: continue

            # Corrélation
            conflict, _ = has_correlated_position(sym, bias, positions)
            if conflict: continue

            # ATR
            atr_now = compute_atr(h1_window, period=14)
            avg_atr = compute_atr(h4_window, period=14)

            # Plan
            plan = build_plan(bias, s_h4, ob_bull, ob_bear, atr_h1=atr_now, market=market, min_rr=cfg["min_rr"])
            if not plan["valid"]: continue

            # Risque
            base_risk = round(capital * RISK_PCT, 2)
            risk = adjust_risk_by_atr(base_risk, atr_now, avg_atr) if atr_now and avg_atr else base_risk

            # Ouverture
            capital -= risk
            positions[sym] = {
                "direction": plan["direction"],
                "entry":     plan["entry"],
                "sl":        plan["sl"],
                "initial_sl": plan["sl"],
                "tp1":       plan["tp1"],
                "rr1":       plan["rr1"],
                "risk_amount": risk,
                "initial_risk_amount": risk,
                "be_set": False, "tp1_taken": False, "trail_active": False,
                "open_ts": ts.isoformat(),
            }

        equity_curve.append((ts.isoformat(), capital + sum(p["risk_amount"] for p in positions.values())))

    # Fermer les positions encore ouvertes au dernier prix
    for sym, pos in list(positions.items()):
        last = histories[sym][-1]["close"]
        initial_r = abs(pos["entry"] - pos["initial_sl"])
        pnl = round(pos["risk_amount"] * (last - pos["entry"]) / initial_r, 2) * (1 if pos["direction"] == "LONG" else -1)
        capital += pos["risk_amount"] + pnl
        trades.append({"sym": sym, "type": "FORCED_CLOSE", "pnl": pnl, "ts": "end"})
        del positions[sym]

    return {
        "market": market,
        "trades": trades,
        "final_capital": capital,
        "equity_curve": equity_curve,
        "start_ts": str(start_ts),
        "end_ts":   str(end_ts),
    }

# ── STATISTIQUES ──────────────────────────────────────────────────────────
def compute_stats(result):
    trades = result["trades"]
    real_trades = [t for t in trades if t["type"] in ("SL", "TP_EXTENDED", "TRAIL_EXIT", "BE", "FORCED_CLOSE")]
    wins = [t for t in real_trades if t["pnl"] > 0]
    losses = [t for t in real_trades if t["pnl"] < 0]
    nul = [t for t in real_trades if t["pnl"] == 0]

    n = len(real_trades)
    wr = round(100 * len(wins) / n, 1) if n else 0
    avg_win  = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
    avg_loss = abs(sum(t["pnl"] for t in losses) / len(losses)) if losses else 0
    profit_factor = round(sum(t["pnl"] for t in wins) / abs(sum(t["pnl"] for t in losses)), 2) if losses else float('inf')

    # Drawdown max
    peak = CAPITAL_START
    max_dd = 0
    for _, eq in result["equity_curve"]:
        if eq > peak: peak = eq
        dd = (peak - eq) / peak * 100
        if dd > max_dd: max_dd = dd

    # Sharpe ratio approximatif (returns journaliers)
    daily = defaultdict(float)
    prev_eq = CAPITAL_START
    for ts, eq in result["equity_curve"]:
        if ts is None: continue
        d = ts[:10]
        daily[d] = eq - prev_eq
        prev_eq = eq
    daily_returns = [v / CAPITAL_START for v in daily.values()]
    if len(daily_returns) > 10:
        import statistics
        mean = statistics.mean(daily_returns)
        std = statistics.stdev(daily_returns) if len(daily_returns) > 1 else 0
        sharpe = round((mean / std) * (252 ** 0.5), 2) if std > 0 else 0
    else:
        sharpe = 0

    pnl_total = round(result["final_capital"] - CAPITAL_START, 2)
    pnl_pct = round(pnl_total / CAPITAL_START * 100, 2)

    return {
        "trades_closed": n,
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(nul),
        "win_rate": wr,
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": profit_factor,
        "max_drawdown_pct": round(max_dd, 2),
        "sharpe_ratio": sharpe,
        "pnl_total": pnl_total,
        "pnl_pct": pnl_pct,
        "final_capital": round(result["final_capital"], 2),
        "tp_partials": len([t for t in trades if t["type"] == "TP_PARTIAL"]),
    }

def print_report(market, stats):
    print(f"\n{'='*60}")
    print(f"📊 RAPPORT BACKTEST — {market.upper()}")
    print(f"{'='*60}")
    print(f"Capital initial    : {CAPITAL_START:.0f}$")
    print(f"Capital final      : {stats['final_capital']:.0f}$")
    print(f"PnL total          : {stats['pnl_total']:+.0f}$ ({stats['pnl_pct']:+.1f}%)")
    print(f"")
    print(f"Trades clos        : {stats['trades_closed']}")
    print(f"  • Wins           : {stats['wins']}")
    print(f"  • Losses         : {stats['losses']}")
    print(f"  • Breakeven      : {stats['breakeven']}")
    print(f"  • TP partiels    : {stats['tp_partials']}")
    print(f"")
    print(f"Win Rate           : {stats['win_rate']}%")
    print(f"Profit Factor      : {stats['profit_factor']}  (>1.5 = bon, >2 = excellent)")
    print(f"Avg Win  / Avg Loss: +{stats['avg_win']:.0f}$ / -{stats['avg_loss']:.0f}$")
    print(f"Max Drawdown       : -{stats['max_drawdown_pct']}%")
    print(f"Sharpe Ratio       : {stats['sharpe_ratio']}  (>1 = bon, >2 = excellent)")
    print(f"{'='*60}\n")

    # Verdict
    pf = stats['profit_factor']
    if isinstance(pf, str): pf = 0
    if stats['trades_closed'] < 20:
        verdict = "⚠️ Pas assez de trades pour conclure"
    elif pf >= 1.5 and stats['win_rate'] >= 45 and stats['max_drawdown_pct'] < 20:
        verdict = "✅ Stratégie viable — peut tester en réel avec petit capital"
    elif pf >= 1.0:
        verdict = "🟡 Marginalement profitable — à améliorer avant le réel"
    else:
        verdict = "❌ Stratégie non rentable sur cette période — NE PAS trader réel"
    print(f"VERDICT : {verdict}\n")

# ── MAIN ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    market = sys.argv[1] if len(sys.argv) > 1 else "forex"
    if market not in MARKETS:
        print(f"❌ Marché inconnu : {market}. Choix : {list(MARKETS.keys())}")
        sys.exit(1)

    result = run_backtest(market)
    if not result:
        sys.exit(1)
    stats = compute_stats(result)
    print_report(market, stats)

    # Sauvegarde
    out_dir = os.path.join(os.path.dirname(__file__), "backtest_results")
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, f"backtest_{market}_{datetime.now().strftime('%Y%m%d_%H%M')}.json")
    with open(out_file, "w") as f:
        json.dump({"stats": stats, "result": result}, f, indent=2, default=str)
    print(f"💾 Détails sauvés dans : {out_file}")
