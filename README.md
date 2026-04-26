# ICT/SMC Trading Bot 24/7

Bot de paper trading ICT/SMC tournant via GitHub Actions toutes les 5 minutes.

## Actifs suivis
- BTC/USD, ETH/USD, SOL/USD (Kraken)

## Stratégie
- Analyse multi-timeframe : H4 + H1 + M5
- Market Structure Shift (MSS) + Order Blocks + FVG
- Risk : 2% par trade | R:R minimum : 2.0

## Notifications
Via ntfy.sh — topic configuré en secret GitHub (`NTFY_TOPIC`)
