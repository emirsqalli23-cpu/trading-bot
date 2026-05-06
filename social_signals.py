#!/usr/bin/env python3
"""
Signaux sociaux et sentiment de marché — sources gratuites publiques.

Modules :
1. Crypto Fear & Greed Index (alternative.me) — sentiment crypto sur 100
2. Truth Social Trump posts — détection annonces qui bougent les marchés
3. Reddit r/wallstreetbets — pulse du sentiment retail extrême

Toutes les API sont gratuites, sans clé requise (à part Reddit qui accepte
les User-Agent avec un bot identifié).

Usage :
    from social_signals import (
        fetch_crypto_fear_greed,    # 0-100, < 25 = peur extrême, > 75 = greed
        fetch_trump_recent_posts,    # liste de posts < 2h avec analyse
        detect_market_shock_social,  # True si shock social détecté
    )
"""

import json, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta
import os, re

UA = {"User-Agent": "Mozilla/5.0 (Trading Bot Research; contact via ntfy)"}

# ── 1. CRYPTO FEAR & GREED INDEX ──────────────────────────────────────────
# Source : alternative.me/api/fng
# Valeur 0-100 calculée depuis : volatilité, momentum, social media, dominance BTC
def fetch_crypto_fear_greed():
    """
    Renvoie un dict :
      {"value": 65, "label": "Greed", "verdict": "BULLISH"|"BEARISH"|"NEUTRAL"}

    Interprétation :
    - 0-24  : Extreme Fear → contrarian BUY (les retail vendent par panique)
    - 25-49 : Fear         → prudence
    - 50-54 : Neutral
    - 55-74 : Greed        → prudence (le top peut être proche)
    - 75-100: Extreme Greed → contrarian SELL (le retail achète sans réfléchir)
    """
    try:
        url = "https://api.alternative.me/fng/?limit=1"
        req = urllib.request.Request(url, headers=UA)
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        d = data["data"][0]
        v = int(d["value"])
        if   v <= 20: verdict = "EXTREME_FEAR"   # contrarian BUY
        elif v <= 44: verdict = "FEAR"
        elif v <= 55: verdict = "NEUTRAL"
        elif v <= 74: verdict = "GREED"
        else:         verdict = "EXTREME_GREED"  # contrarian SELL
        return {"value": v, "label": d["value_classification"], "verdict": verdict,
                "timestamp": d.get("timestamp")}
    except Exception as e:
        return {"value": None, "label": "?", "verdict": "NEUTRAL", "error": str(e)}


def fear_greed_signal_for_crypto(fg, direction):
    """
    Donne un signal contrarien pour la crypto :
    - Si on veut LONG quand fear extrême → ✅ favorable (les retail vendent par panique)
    - Si on veut SHORT quand greed extrême → ✅ favorable
    - Inverse → ⚠️ contre la marée retail (mais peut quand même marcher)
    """
    if not fg.get("value"):
        return True, "Fear & Greed indisponible", 1.0
    v = fg["value"]
    verdict = fg["verdict"]
    if direction == "LONG":
        if verdict == "EXTREME_FEAR":  return True, f"💎 Peur extrême ({v}/100) — opportunité d'achat contrarien", 1.2
        if verdict == "EXTREME_GREED": return True, f"⚠️ Greed extrême ({v}/100) — risque de top", 0.6
    elif direction == "SHORT":
        if verdict == "EXTREME_GREED": return True, f"💎 Greed extrême ({v}/100) — opportunité de vente contrarien", 1.2
        if verdict == "EXTREME_FEAR":  return True, f"⚠️ Peur extrême ({v}/100) — risque de bottom", 0.6
    return True, f"Sentiment {verdict.lower()} ({v}/100)", 1.0


# ── 2. TRUTH SOCIAL TRUMP SCRAPER ─────────────────────────────────────────
# Source : truthsocial.com/users/realDonaldTrump (posts publics, pas d'API officielle)
# On utilise l'endpoint web JSON public.
# ⚠️ Truth Social peut bloquer les requêtes — on a un fallback gracieux.

TRUMP_KEYWORDS_BULLISH_USD = ["tariff", "sanction", "ban", "no deal", "withdraw", "stop"]
TRUMP_KEYWORDS_BEARISH_USD = ["deal", "agreement", "lower rates", "fed cut", "stimulus"]
TRUMP_KEYWORDS_GOLD_UP    = ["war", "tariff", "crisis", "china", "russia", "iran"]

def fetch_trump_recent_posts(max_age_minutes=180):
    """
    Récupère les posts Trump des X dernières minutes.
    Renvoie une liste de {"text": ..., "time": ..., "url": ...}.

    NOTE : Truth Social a une API publique fragile. Si elle échoue, on renvoie [].
    Plan B : nitter.net (mirror Twitter) si Trump utilise Twitter aussi.
    """
    posts = []
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)
    # Endpoint public Truth Social v1 (accounts/lookup + statuses)
    try:
        # Acct ID Trump = "107780257626128497"
        url = "https://truthsocial.com/api/v1/accounts/107780257626128497/statuses?limit=20"
        req = urllib.request.Request(url, headers=UA)
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        for s in data:
            try:
                t = datetime.fromisoformat(s["created_at"].replace("Z", "+00:00"))
                if t < cutoff: continue
                # Strip HTML tags
                text = re.sub(r"<[^>]+>", " ", s.get("content", "")).strip()
                if not text: continue
                posts.append({"text": text[:280], "time": t.isoformat(), "url": s.get("url")})
            except Exception: continue
    except Exception as e:
        # Truth Social peut bloquer — log silencieux et fallback vide
        print(f"  ↪ Truth Social KO : {e}", flush=True)
    return posts


def analyze_trump_post(text):
    """
    Détermine si un post Trump est un signal de marché et son impact estimé.
    Renvoie : {"impact": "HIGH"/"MEDIUM"/"NONE", "direction_usd": "UP"/"DOWN"/"NEUTRAL", "tags": [...]}
    """
    t = text.lower()
    tags = []
    impact = "NONE"
    direction_usd = "NEUTRAL"

    # Détection bull USD
    bull_hits = [kw for kw in TRUMP_KEYWORDS_BULLISH_USD if kw in t]
    bear_hits = [kw for kw in TRUMP_KEYWORDS_BEARISH_USD if kw in t]
    gold_hits = [kw for kw in TRUMP_KEYWORDS_GOLD_UP    if kw in t]

    if bull_hits:
        tags.extend(bull_hits)
        direction_usd = "UP"
        impact = "MEDIUM"
    if bear_hits:
        tags.extend(bear_hits)
        direction_usd = "DOWN"
        impact = "MEDIUM"
    if gold_hits:
        tags.extend(["gold:up"])
        if impact == "NONE": impact = "MEDIUM"

    # Mots qui amplifient (urgence)
    if any(w in t for w in ["breaking", "today", "now", "immediately", "executive order"]):
        impact = "HIGH" if impact != "NONE" else "MEDIUM"

    return {"impact": impact, "direction_usd": direction_usd, "tags": tags}


def detect_market_shock_social(max_age_minutes=60):
    """
    Vérifie s'il y a un signal social récent qui pourrait bouger les marchés.
    Renvoie (True, message) si shock détecté, sinon (False, None).
    """
    posts = fetch_trump_recent_posts(max_age_minutes)
    for p in posts:
        analysis = analyze_trump_post(p["text"])
        if analysis["impact"] == "HIGH":
            return True, f"🚨 Trump post HIGH impact : {p['text'][:80]}... → USD {analysis['direction_usd']}"
    return False, None


# ── 3. REDDIT r/wallstreetbets PULSE ──────────────────────────────────────
def fetch_wsb_top_posts(limit=10):
    """
    Récupère les top posts de r/wallstreetbets (sentiment retail extrême).
    Renvoie liste de {"title": ..., "score": ..., "num_comments": ...}.

    Pas d'auth requise pour la lecture publique JSON.
    """
    try:
        url = f"https://www.reddit.com/r/wallstreetbets/hot.json?limit={limit}"
        req = urllib.request.Request(url, headers=UA)
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        out = []
        for child in data.get("data", {}).get("children", []):
            d = child.get("data", {})
            out.append({
                "title":        d.get("title", "")[:120],
                "score":        d.get("score", 0),
                "comments":     d.get("num_comments", 0),
                "flair":        d.get("link_flair_text"),
            })
        return out
    except Exception as e:
        return []


# ── CLI test ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n🟢 CRYPTO FEAR & GREED")
    print("="*60)
    fg = fetch_crypto_fear_greed()
    print(f"   Valeur     : {fg.get('value','?')}/100")
    print(f"   Label      : {fg.get('label','?')}")
    print(f"   Verdict    : {fg.get('verdict','?')}")
    if fg.get("value"):
        for d in ["LONG", "SHORT"]:
            ok, msg, factor = fear_greed_signal_for_crypto(fg, d)
            print(f"   Signal {d:5}: factor x{factor} — {msg}")

    print("\n🟠 TRUTH SOCIAL — posts Trump (3h)")
    print("="*60)
    posts = fetch_trump_recent_posts(180)
    print(f"   Posts récents : {len(posts)}")
    for p in posts[:5]:
        analysis = analyze_trump_post(p["text"])
        emoji = {"HIGH":"🚨","MEDIUM":"⚠️","NONE":"💬"}.get(analysis["impact"],"?")
        print(f"   {emoji} {p['time'][:16]} — impact {analysis['impact']} | USD {analysis['direction_usd']}")
        print(f"      \"{p['text'][:100]}\"")

    print("\n🔴 REDDIT r/wallstreetbets")
    print("="*60)
    wsb = fetch_wsb_top_posts(5)
    for p in wsb[:5]:
        print(f"   👍 {p['score']:5} | 💬 {p['comments']:4} | {p['flair'] or '?':15} | {p['title'][:60]}")

    print("\n🚨 Détection shock social (60 min)")
    print("="*60)
    shock, msg = detect_market_shock_social(60)
    print(f"   Shock détecté : {shock}")
    if msg: print(f"   Message       : {msg}")
