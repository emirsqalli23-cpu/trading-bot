#!/usr/bin/env python3
"""
IG Markets Broker — connecteur API REST.

Permet au bot de :
- Se connecter à IG (LIVE ou DÉMO)
- Lire les prix temps réel depuis le broker
- Récupérer le solde, les positions ouvertes
- Ouvrir / fermer une position (en DRY_RUN par défaut = simulé, sécurisé)

⚠️ SÉCURITÉ : DRY_RUN=True par défaut. Aucun ordre n'est envoyé tant que
le flag IG_DRY_RUN n'est pas explicitement mis à "false" dans l'environnement.

Usage :
    broker = IGBroker(environment="demo", dry_run=True)
    broker.login()
    broker.get_account_summary()
    broker.search_market("EUR/USD")
    broker.open_position(epic="CS.D.EURUSD.MINI.IP", direction="BUY", size=1)
    broker.logout()
"""

import json, os, urllib.request, urllib.error
from datetime import datetime, timezone

# ── CREDENTIALS (lus depuis l'environnement, fallback hardcodé local seulement) ──
def _get(key, default=""):
    return os.environ.get(key, default)

IG_LIVE = {
    "url":      "https://api.ig.com/gateway/deal",
    "api_key":  _get("IG_API_KEY",      "43abe05b46d43c4ffe2585c79eef9309bc751b64"),
    "username": _get("IG_USERNAME",     "SQALEM80496713"),
    "password": _get("IG_PASSWORD",     "Emirmonbebe23@"),
}
IG_DEMO = {
    "url":      "https://demo-api.ig.com/gateway/deal",
    "api_key":  _get("IG_DEMO_API_KEY",  "b5b58c0a114e356a7ed55308c4dfbda245b3fd11"),
    "username": _get("IG_DEMO_USERNAME", ""),  # à remplir quand IG aura traité la requête
    "password": _get("IG_DEMO_PASSWORD", ""),
}

# Mode safe : par défaut, aucun ordre n'est envoyé à IG (uniquement loggé)
DRY_RUN_DEFAULT = _get("IG_DRY_RUN", "true").lower() != "false"

class IGAuthError(Exception):    pass
class IGAPIError(Exception):     pass
class IGNotConnected(Exception): pass

# ── BROKER ────────────────────────────────────────────────────────────────
class IGBroker:
    def __init__(self, environment="demo", dry_run=None):
        """
        environment : "live" ou "demo"
        dry_run     : True = simule les ordres sans les envoyer (par défaut sûr)
        """
        if environment not in ("live", "demo"):
            raise ValueError("environment doit être 'live' ou 'demo'")
        self.env = environment
        self.cfg = IG_LIVE if environment == "live" else IG_DEMO
        self.dry_run = dry_run if dry_run is not None else DRY_RUN_DEFAULT
        self.cst = None
        self.xst = None
        self.account_id = None
        self.session_data = None

    # ─── Login / Logout ──────────────────────────────────────────────────
    def login(self):
        """Se connecte et stocke les tokens CST + X-SECURITY-TOKEN."""
        if not self.cfg["username"] or not self.cfg["password"]:
            raise IGAuthError(
                f"Credentials {self.env.upper()} manquants. "
                "Définir IG_USERNAME / IG_PASSWORD (live) ou IG_DEMO_USERNAME / IG_DEMO_PASSWORD (demo)."
            )
        payload = json.dumps({
            "identifier": self.cfg["username"],
            "password":   self.cfg["password"],
        }).encode()
        req = urllib.request.Request(
            f"{self.cfg['url']}/session",
            data=payload,
            headers={
                "Content-Type": "application/json; charset=UTF-8",
                "Accept":       "application/json; charset=UTF-8",
                "X-IG-API-KEY": self.cfg["api_key"],
                "Version":      "2",
            },
            method="POST",
        )
        try:
            resp = urllib.request.urlopen(req, timeout=15)
            self.session_data = json.loads(resp.read())
            self.cst = resp.headers.get("CST", "")
            self.xst = resp.headers.get("X-SECURITY-TOKEN", "")
            self.account_id = self.session_data.get("currentAccountId")
            return self.session_data
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise IGAuthError(f"HTTP {e.code} — {body}")

    def logout(self):
        """Ferme proprement la session."""
        if not self.cst: return
        try:
            self._request("DELETE", "/session", version="1")
        except Exception: pass
        self.cst = self.xst = self.account_id = None

    def __enter__(self):
        self.login(); return self
    def __exit__(self, *a):
        self.logout()

    # ─── HTTP wrapper authentifié ────────────────────────────────────────
    def _request(self, method, endpoint, payload=None, version="1"):
        if not self.cst:
            raise IGNotConnected("Appelle .login() d'abord.")
        headers = {
            "Accept":           "application/json; charset=UTF-8",
            "X-IG-API-KEY":     self.cfg["api_key"],
            "CST":              self.cst,
            "X-SECURITY-TOKEN": self.xst,
            "Version":          version,
        }
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json; charset=UTF-8"
            data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{self.cfg['url']}{endpoint}",
            data=data, headers=headers, method=method,
        )
        try:
            resp = urllib.request.urlopen(req, timeout=15)
            body = resp.read()
            if not body: return {}
            return json.loads(body)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise IGAPIError(f"HTTP {e.code} {endpoint} — {body[:200]}")

    # ─── Account ─────────────────────────────────────────────────────────
    def get_account_summary(self):
        """Renvoie un résumé du compte courant : solde, marge, dispo, statut."""
        accs = self._request("GET", "/accounts", version="1").get("accounts", [])
        for acc in accs:
            if acc.get("accountId") == self.account_id:
                bal = acc.get("balance") or {}
                return {
                    "account_id":  acc.get("accountId"),
                    "name":        acc.get("accountName"),
                    "type":        acc.get("accountType"),
                    "currency":    acc.get("currency"),
                    "status":      acc.get("status"),
                    "tradable":    acc.get("status") == "ENABLED",
                    "balance":     float(bal.get("balance", 0) or 0),
                    "available":   float(bal.get("available", 0) or 0),
                    "deposit":     float(bal.get("deposit", 0) or 0),
                    "profit_loss": float(bal.get("profitLoss", 0) or 0),
                }
        return None

    def get_positions(self):
        """Renvoie la liste des positions ouvertes (avec PnL latent)."""
        r = self._request("GET", "/positions", version="2")
        out = []
        for p in r.get("positions", []):
            pos = p.get("position", {}) or {}
            mkt = p.get("market", {}) or {}
            out.append({
                "deal_id":    pos.get("dealId"),
                "epic":       mkt.get("epic"),
                "instrument": mkt.get("instrumentName"),
                "direction":  pos.get("direction"),
                "size":       pos.get("size"),
                "open_level": pos.get("openLevel"),
                "stop_level": pos.get("stopLevel"),
                "limit_level": pos.get("limitLevel"),
                "currency":   pos.get("currency"),
                "current_bid": mkt.get("bid"),
                "current_offer": mkt.get("offer"),
            })
        return out

    # ─── Markets ─────────────────────────────────────────────────────────
    def search_market(self, search_term):
        """Cherche un instrument et renvoie les 5 premiers résultats."""
        import urllib.parse
        # safe="" = encode TOUT, y compris les / (sinon 404)
        q = urllib.parse.quote(search_term, safe="")
        r = self._request("GET", f"/markets?searchTerm={q}", version="1")
        markets = r.get("markets", [])
        return [{
            "epic":       m.get("epic"),
            "name":       m.get("instrumentName"),
            "type":       m.get("instrumentType"),
            "bid":        m.get("bid"),
            "offer":      m.get("offer"),
            "status":     m.get("marketStatus"),
            "expiry":     m.get("expiry"),
        } for m in markets[:5]]

    def get_market_details(self, epic):
        """Renvoie les règles de trading pour un epic (taille mini, marge, etc.)."""
        r = self._request("GET", f"/markets/{epic}", version="3")
        return {
            "epic":          epic,
            "name":          r.get("instrument", {}).get("name"),
            "type":          r.get("instrument", {}).get("type"),
            "min_size":      r.get("dealingRules", {}).get("minDealSize", {}).get("value"),
            "min_stop":      r.get("dealingRules", {}).get("minNormalStopOrLimitDistance", {}).get("value"),
            "min_stop_unit": r.get("dealingRules", {}).get("minNormalStopOrLimitDistance", {}).get("unit"),
            "currency":      (r.get("instrument", {}).get("currencies") or [{}])[0].get("code"),
            "bid":           r.get("snapshot", {}).get("bid"),
            "offer":         r.get("snapshot", {}).get("offer"),
            "scaling_factor": r.get("snapshot", {}).get("scalingFactor"),
            "raw":            r,  # garde tout pour debug
        }

    # ─── Trading (avec DRY_RUN obligatoire par défaut) ───────────────────
    def open_position(self, epic, direction, size, stop_distance=None,
                      limit_distance=None, currency_code="EUR", confirm=True):
        """
        Ouvre une position.
        ⚠️ Si dry_run=True (défaut), simule sans envoyer l'ordre.

        Args:
            epic            : ID IG (ex: "CS.D.EURUSD.MINI.IP")
            direction       : "BUY" (LONG) ou "SELL" (SHORT)
            size            : taille (ex: 1 = 1 contrat MINI)
            stop_distance   : distance du SL en pips (ex: 30)
            limit_distance  : distance du TP en pips (ex: 60)
            currency_code   : devise (par défaut EUR)
        """
        order = {
            "epic":          epic,
            "direction":     direction.upper(),
            "size":          size,
            "orderType":     "MARKET",
            "currencyCode":  currency_code,
            "forceOpen":     True,
            "guaranteedStop": False,
            "expiry":        "-",
        }
        if stop_distance:  order["stopDistance"]  = stop_distance
        if limit_distance: order["limitDistance"] = limit_distance

        if self.dry_run:
            print(f"🔒 [DRY_RUN] Simulation ordre IG : {direction} {size} sur {epic}")
            print(f"   SL distance: {stop_distance} | TP distance: {limit_distance}")
            return {"dry_run": True, "order": order, "would_execute": True}

        # ⚠️ Vrai ordre — désactivé par défaut
        r = self._request("POST", "/positions/otc", payload=order, version="2")
        deal_ref = r.get("dealReference")
        if confirm and deal_ref:
            return self._confirm_deal(deal_ref)
        return r

    def _confirm_deal(self, deal_ref):
        """Confirme l'exécution d'un ordre (statut, deal_id, prix réel)."""
        return self._request("GET", f"/confirms/{deal_ref}", version="1")

    def close_position(self, deal_id, direction, size):
        """Ferme une position ouverte (envoie un ordre opposé)."""
        order = {
            "dealId":    deal_id,
            "direction": "SELL" if direction.upper() == "BUY" else "BUY",
            "size":      size,
            "orderType": "MARKET",
        }
        if self.dry_run:
            print(f"🔒 [DRY_RUN] Simulation fermeture {deal_id}")
            return {"dry_run": True, "order": order}
        return self._request("POST", "/positions/otc", payload=order, version="1")


# ── Mapping bot symbol → EPIC IG ──
# Les contrats CEFM sont les MINI (taille réduite, idéal retail).
# Les "SUN..." sont les versions weekend (tradables sam/dim).
SYMBOL_TO_EPIC = {
    # Forex MINI (semaine)
    "EURUSD=X":  "CS.D.EURUSD.CEFM.IP",
    "GBPUSD=X":  "CS.D.GBPUSD.CEFM.IP",
    # Forex Weekend (sam/dim)
    "EURUSD=X.WE": "IX.D.SUNEURUSD.CEF.IP",
    "GBPUSD=X.WE": "IX.D.SUNGBPUSD.CEF.IP",
    # Or
    "GC=F":      "CS.D.CFDGOLD.CFDGC.IP",
    "XAUUSD=X":  "CS.D.CFDGOLD.CFDGC.IP",
    # Crypto
    "XBTUSD":    "CS.D.BITCOIN.CEFM.IP",
    "ETHUSD":    "CS.D.ETHUSD.CFM.IP",
    "SOLUSD":    "CS.D.SOLUSD.CFM.IP",
}

def bot_symbol_to_epic(sym, weekend=False):
    """Convertit un symbole du bot vers un EPIC IG.
    weekend=True : utilise la version SUN (tradable sam/dim) si dispo.
    """
    if weekend:
        we = SYMBOL_TO_EPIC.get(sym + ".WE")
        if we: return we
    return SYMBOL_TO_EPIC.get(sym)


# ── CLI test (lecture seule par sécurité) ─────────────────────────────────
if __name__ == "__main__":
    import sys
    env = sys.argv[1] if len(sys.argv) > 1 else "live"
    print(f"\n{'='*60}\n🔌 Test connexion IG {env.upper()}\n{'='*60}\n")

    with IGBroker(environment=env, dry_run=True) as ig:
        summary = ig.get_account_summary()
        print(f"💼 Compte : {summary['account_id']} ({summary['type']})")
        print(f"   Statut       : {'✅' if summary['tradable'] else '⚠️'} {summary['status']}")
        print(f"   Devise       : {summary['currency']}")
        print(f"   💰 Solde     : {summary['balance']}€")
        print(f"   🏦 Disponible: {summary['available']}€")
        print(f"   🔒 Marge     : {summary['deposit']}€")

        print(f"\n📈 Positions ouvertes ({len(ig.get_positions())}):")
        for p in ig.get_positions():
            print(f"   • {p['instrument']} | {p['direction']} {p['size']} | open@{p['open_level']}")

        print(f"\n🔍 Recherche prix EUR/USD :")
        for m in ig.search_market("EUR/USD"):
            print(f"   • {m['name']:30} | bid={m['bid']} offer={m['offer']} | {m['epic']}")

        print(f"\n🔒 Test DRY_RUN d'un ordre LONG EUR/USD (rien envoyé pour de vrai) :")
        r = ig.open_position(epic="CS.D.EURUSD.CEFM.IP", direction="BUY", size=1,
                             stop_distance=30, limit_distance=60)
        print(f"   {r}")
