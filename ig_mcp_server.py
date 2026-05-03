#!/usr/bin/env python3
"""
Serveur MCP pour piloter IG Markets depuis Claude.

Expose les fonctions du broker IG comme outils MCP que Claude peut appeler :
- ig_account_summary  : solde, statut, marge
- ig_positions        : positions ouvertes avec PnL
- ig_search           : chercher un instrument
- ig_market_details   : détails d'un EPIC (taille mini, marge requise)
- ig_open_position    : ouvrir un trade (DRY_RUN par défaut !)
- ig_close_position   : fermer un trade
- ig_prices           : prix temps réel d'un EPIC

⚠️ SÉCURITÉ : Toujours en DRY_RUN par défaut. Pour activer le trading réel :
    export IG_DRY_RUN=false

Configuration dans ~/Library/Application Support/Claude/claude_desktop_config.json :
    "mcpServers": {
      "ig-trading": {
        "command": "python3",
        "args": ["/Users/macbook/trading-bot-pro/ig_mcp_server.py"],
        "env": {
          "IG_ENV": "live",
          "IG_DRY_RUN": "true"
        }
      }
    }
"""

import json, sys, os, asyncio
from typing import Any

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    import mcp.types as types
except ImportError:
    print("❌ Le package 'mcp' n'est pas installé.", file=sys.stderr)
    print("   Requis : Python ≥ 3.10. Installation :", file=sys.stderr)
    print("   1. Installer Python 3.10+ via : brew install python@3.11", file=sys.stderr)
    print("   2. Puis : python3.11 -m pip install mcp", file=sys.stderr)
    print("   3. Et adapter le 'command' dans claude_desktop_config.json", file=sys.stderr)
    sys.exit(1)

from ig_broker import IGBroker, IGAuthError, IGAPIError, bot_symbol_to_epic

# ── Configuration ─────────────────────────────────────────────────────────
ENV = os.environ.get("IG_ENV", "live")  # "live" ou "demo"
DRY_RUN = os.environ.get("IG_DRY_RUN", "true").lower() != "false"

# Singleton broker (re-login à chaque tool call pour éviter sessions expirées)
def _new_broker():
    return IGBroker(environment=ENV, dry_run=DRY_RUN)

# ── Serveur MCP ───────────────────────────────────────────────────────────
server = Server("ig-trading")

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="ig_account_summary",
            description="Récupère le résumé du compte IG : solde, marge, P&L, statut.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="ig_positions",
            description="Liste les positions ouvertes sur le compte IG avec leurs détails.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="ig_search",
            description="Cherche un instrument financier sur IG (ex: 'EUR/USD', 'Gold', 'Bitcoin'). Renvoie les EPICs et prix temps réel.",
            inputSchema={
                "type": "object",
                "properties": {"search_term": {"type": "string"}},
                "required": ["search_term"],
            },
        ),
        types.Tool(
            name="ig_market_details",
            description="Renvoie les règles de trading d'un EPIC IG : taille mini, distance SL min, marge requise.",
            inputSchema={
                "type": "object",
                "properties": {"epic": {"type": "string"}},
                "required": ["epic"],
            },
        ),
        types.Tool(
            name="ig_open_position",
            description="Ouvre une position sur IG. DRY_RUN par défaut (simulation). Pour activer en réel, définir IG_DRY_RUN=false.",
            inputSchema={
                "type": "object",
                "properties": {
                    "epic":           {"type": "string", "description": "EPIC IG (ex: CS.D.EURUSD.CEFM.IP)"},
                    "direction":      {"type": "string", "enum": ["BUY", "SELL"]},
                    "size":           {"type": "number", "description": "Taille (ex: 1 pour 1 contrat MINI)"},
                    "stop_distance":  {"type": "number", "description": "Distance SL en pips"},
                    "limit_distance": {"type": "number", "description": "Distance TP en pips"},
                },
                "required": ["epic", "direction", "size"],
            },
        ),
        types.Tool(
            name="ig_close_position",
            description="Ferme une position ouverte sur IG.",
            inputSchema={
                "type": "object",
                "properties": {
                    "deal_id":   {"type": "string"},
                    "direction": {"type": "string", "enum": ["BUY", "SELL"]},
                    "size":      {"type": "number"},
                },
                "required": ["deal_id", "direction", "size"],
            },
        ),
        types.Tool(
            name="ig_status",
            description="Statut actuel du connecteur IG (env, dry_run, connecté ou non).",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    try:
        if name == "ig_status":
            return [types.TextContent(type="text", text=json.dumps({
                "environment": ENV,
                "dry_run":     DRY_RUN,
                "endpoint":    f"https://{'demo-' if ENV == 'demo' else ''}api.ig.com/gateway/deal",
            }, indent=2))]

        with _new_broker() as ig:
            if name == "ig_account_summary":
                result = ig.get_account_summary()
            elif name == "ig_positions":
                result = ig.get_positions()
            elif name == "ig_search":
                result = ig.search_market(arguments["search_term"])
            elif name == "ig_market_details":
                r = ig.get_market_details(arguments["epic"])
                # On enlève le "raw" trop verbeux pour la réponse MCP
                r.pop("raw", None)
                result = r
            elif name == "ig_open_position":
                result = ig.open_position(
                    epic=arguments["epic"],
                    direction=arguments["direction"],
                    size=arguments["size"],
                    stop_distance=arguments.get("stop_distance"),
                    limit_distance=arguments.get("limit_distance"),
                )
            elif name == "ig_close_position":
                result = ig.close_position(
                    deal_id=arguments["deal_id"],
                    direction=arguments["direction"],
                    size=arguments["size"],
                )
            else:
                result = {"error": f"Tool inconnu : {name}"}

        return [types.TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

    except IGAuthError as e:
        return [types.TextContent(type="text", text=f"❌ Erreur auth IG : {e}")]
    except IGAPIError as e:
        return [types.TextContent(type="text", text=f"❌ Erreur API IG : {e}")]
    except Exception as e:
        return [types.TextContent(type="text", text=f"❌ Erreur : {type(e).__name__} : {e}")]

# ── Run ───────────────────────────────────────────────────────────────────
async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())
