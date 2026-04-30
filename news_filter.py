#!/usr/bin/env python3
"""
News Filter - protège le bot contre les news imprévues.

3 protections :
1. Calendrier économique ForexFactory : bloque les events high-impact (NFP, FOMC, CPI...)
2. RSS Investing.com / ForexLive : detecte les news temps réel
3. Détection "shock" : ferme positions si news catastrophique
"""

import json, urllib.request, time
from datetime import datetime, timezone, timedelta
from xml.etree import ElementTree as ET

# ── Mapping devise <-> mots clefs (pour matcher news avec position) ───────
CURRENCY_KEYWORDS = {
    "USD": ["fed", "powell", "fomc", "us cpi", "nfp", "jobless claim",
            "us gdp", "treasury", "yellen", "bls", "ism", "retail sales"],
    "EUR": ["ecb", "lagarde", "eurozone", "germany", "france",
            "italy", "spain", "european central"],
    "GBP": ["boe", "bailey", "uk cpi", "uk gdp", "britain", "pound",
            "bank of england", "downing street"],
    "JPY": ["boj", "ueda", "japan", "yen"],
    "XAU": ["gold", "fed", "inflation", "war", "crisis", "geopolitical"],
}

# Mots clefs qui declenchent un alert "shock"
SHOCK_KEYWORDS = [
    "war", "attack", "invasion", "default", "crisis", "crash", "halted",
    "emergency", "shock", "surprise", "unexpected", "breaking",
    "ban", "sanctions", "downgrade", "collapse", "panic",
    "rate cut", "rate hike", "intervention",
]

RSS_FEEDS = [
    "https://www.forexlive.com/feed/",
    "https://www.investing.com/rss/news_1.rss",       # general
    "https://www.investing.com/rss/news_25.rss",      # forex news
    "https://www.investing.com/rss/news_356.rss",     # commodities
]

# ── Helpers ───────────────────────────────────────────────────────────────
def symbol_to_currencies(symbol):
    """EURUSD=X -> ['EUR', 'USD'] | GC=F -> ['XAU'] | XBTUSD -> ['BTC', 'USD']"""
    sym = symbol.replace("=X", "").replace("=F", "").replace("/", "")
    if symbol in ("GC=F", "XAUUSD=X"):
        return ["XAU", "USD"]
    if len(sym) == 6:
        return [sym[:3], sym[3:]]
    if sym.startswith("XBT") or sym.startswith("BTC"):
        return ["BTC", "USD"]
    if sym.startswith("ETH"):
        return ["ETH", "USD"]
    if sym.startswith("SOL"):
        return ["SOL", "USD"]
    return [sym]

# ── Source 1 : Calendrier économique ForexFactory ─────────────────────────
def fetch_calendar():
    url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=10).read())
    except Exception as e:
        print(f"[news] calendrier KO: {e}")
        return []

def is_high_impact_imminent(symbol, minutes_before=30, minutes_after=30):
    """True si un event HIGH impact tombe dans [-30min, +30min] pour les devises du symbole."""
    currencies = symbol_to_currencies(symbol)
    events = fetch_calendar()
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(minutes=minutes_after)
    window_end   = now + timedelta(minutes=minutes_before)

    for ev in events:
        if ev.get("impact", "").lower() != "high":
            continue
        if ev.get("country") not in currencies:
            continue
        try:
            ev_time = datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))
            if window_start <= ev_time <= window_end:
                return True, f"{ev.get('title','?')} ({ev.get('country')}) à {ev_time.strftime('%H:%M UTC')}"
        except Exception:
            continue
    return False, None

# ── Source 2 : RSS news temps réel ────────────────────────────────────────
def fetch_recent_news(max_age_minutes=15):
    """Récupère les news des X dernières minutes via RSS."""
    news = []
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)

    for url in RSS_FEEDS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            xml = urllib.request.urlopen(req, timeout=10).read()
            root = ET.fromstring(xml)
            for item in root.iter("item"):
                title_el = item.find("title")
                date_el  = item.find("pubDate")
                if title_el is None or date_el is None: continue
                title = (title_el.text or "").strip()
                pub_str = (date_el.text or "").strip()
                try:
                    # Format RSS : "Wed, 01 May 2026 10:32:00 +0000" ou "GMT"
                    pub = datetime.strptime(pub_str[:25], "%a, %d %b %Y %H:%M:%S")
                    pub = pub.replace(tzinfo=timezone.utc)
                    if pub >= cutoff:
                        news.append({"title": title, "date": pub})
                except Exception:
                    continue
        except Exception as e:
            print(f"[news] RSS {url[:40]} KO: {e}")
    return news

def detect_shock(symbol, max_age_minutes=10):
    """
    Détecte une news "shock" récente concernant la devise du symbole.
    Renvoie (True, titre) si trouvé.
    """
    currencies = symbol_to_currencies(symbol)
    keywords = []
    for cur in currencies:
        keywords.extend(CURRENCY_KEYWORDS.get(cur, []))

    news = fetch_recent_news(max_age_minutes)
    for item in news:
        title_lower = item["title"].lower()
        cur_match   = any(kw in title_lower for kw in keywords)
        shock_match = any(kw in title_lower for kw in SHOCK_KEYWORDS)
        if cur_match and shock_match:
            return True, item["title"]
    return False, None

# ── Verdict global ────────────────────────────────────────────────────────
def can_open_position(symbol):
    """
    Renvoie (autorise, raison_de_blocage).
    True = on peut ouvrir, False = on bloque.
    """
    blocked, ev = is_high_impact_imminent(symbol)
    if blocked:
        return False, f"📅 {ev}"

    shock, title = detect_shock(symbol)
    if shock:
        return False, f"🚨 {title[:80]}"

    return True, None

def should_close_positions(symbol):
    """
    A appeler en début de cycle : True si shock news → fermer positions ouvertes.
    """
    shock, title = detect_shock(symbol, max_age_minutes=15)
    if shock:
        return True, title
    return False, None

# ── CLI test ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else "EURUSD=X"
    print(f"=== Test news filter pour {sym} ===")
    print(f"Devises mappées : {symbol_to_currencies(sym)}")
    blocked, ev = is_high_impact_imminent(sym)
    print(f"Event imminent  : {blocked} | {ev}")
    shock, title = detect_shock(sym)
    print(f"Shock detecté   : {shock} | {title}")
    ok, reason = can_open_position(sym)
    print(f"Ouvrable        : {ok} | blocage: {reason}")
