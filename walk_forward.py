#!/usr/bin/env python3
"""
Walk-Forward Validation — teste la stratégie sur 2 périodes distinctes.

Si les 2 périodes donnent des résultats similaires (même PF, même WR),
la stratégie est ROBUSTE — pas une chance sur une seule période.

Si une période est très bonne et l'autre très mauvaise → la stratégie est
fragile (overfittée à une période particulière) → DANGER.
"""

import sys
from datetime import datetime, timezone, timedelta
from backtest import run_backtest, compute_stats, MARKETS

def compare_periods(market):
    print(f"\n{'#'*60}")
    print(f"# WALK-FORWARD VALIDATION : {market.upper()}")
    print(f"{'#'*60}\n")

    now = datetime.now(timezone.utc)

    # Période 1 : il y a 12 mois → il y a 6 mois (passé lointain)
    p1_end   = now - timedelta(days=180)
    # Période 2 : il y a 6 mois → maintenant (passé récent)
    p2_end   = now

    print(f"📅 Période 1 (passé lointain) : {(p1_end - timedelta(days=180)).date()} → {p1_end.date()}")
    print(f"📅 Période 2 (passé récent)   : {(p2_end - timedelta(days=180)).date()} → {p2_end.date()}\n")

    # Run period 1
    r1 = run_backtest(market, days=180, end_date=p1_end, label=f"Periode 1 (lointaine)")
    s1 = compute_stats(r1) if r1 else None

    # Run period 2
    r2 = run_backtest(market, days=180, end_date=p2_end, label=f"Periode 2 (recente)")
    s2 = compute_stats(r2) if r2 else None

    if not s1 or not s2:
        print("❌ Backtest a échoué")
        return

    # Comparaison
    print(f"\n{'='*60}")
    print(f"📊 COMPARAISON DES 2 PÉRIODES — {market.upper()}")
    print(f"{'='*60}")
    print(f"{'Métrique':<25} {'Période 1':<20} {'Période 2':<20}")
    print(f"{'-'*65}")
    print(f"{'Trades clos':<25} {s1['trades_closed']:<20} {s2['trades_closed']:<20}")
    print(f"{'Win Rate':<25} {s1['win_rate']}%{'':<15} {s2['win_rate']}%")
    print(f"{'Profit Factor':<25} {s1['profit_factor']:<20} {s2['profit_factor']:<20}")
    print(f"{'PnL %':<25} {s1['pnl_pct']:+}%{'':<15} {s2['pnl_pct']:+}%")
    print(f"{'Max Drawdown':<25} -{s1['max_drawdown_pct']}%{'':<14} -{s2['max_drawdown_pct']}%")
    print(f"{'Avg Win/Loss':<25} +{s1['avg_win']}/-{s1['avg_loss']}{'':<8} +{s2['avg_win']}/-{s2['avg_loss']}")
    print(f"{'='*60}\n")

    # Verdict
    pf1 = s1['profit_factor'] if isinstance(s1['profit_factor'], (int, float)) else 99
    pf2 = s2['profit_factor'] if isinstance(s2['profit_factor'], (int, float)) else 99
    wr_diff = abs(s1['win_rate'] - s2['win_rate'])

    if s1['trades_closed'] < 10 or s2['trades_closed'] < 10:
        print("⚠️ VERDICT : Pas assez de trades sur une des périodes pour conclure")
    elif pf1 >= 1.3 and pf2 >= 1.3 and wr_diff < 25:
        print("✅ VERDICT : Stratégie ROBUSTE — fonctionne sur les 2 périodes")
        print("   → On peut faire confiance aux résultats du backtest complet.")
    elif pf1 >= 1.0 and pf2 >= 1.0:
        print("🟡 VERDICT : Stratégie fragile — rentable mais résultats variables")
        print("   → À surveiller en live, ne pas miser gros au début.")
    else:
        print("❌ VERDICT : Stratégie FRAGILE — une période est non-rentable")
        print("   → Risque d'overfit, NE PAS trader réel sans plus de tests.")

if __name__ == "__main__":
    market = sys.argv[1] if len(sys.argv) > 1 else "forex"
    if market == "all":
        for m in ["forex", "gold", "crypto"]:
            compare_periods(m)
    else:
        compare_periods(market)
