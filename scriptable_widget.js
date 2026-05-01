// ┌──────────────────────────────────────────────────────────────────┐
// │  Trading Bot Widget — Scriptable iOS                             │
// │  Affiche les 3 bots (Crypto, Or, Forex) + total en direct        │
// │                                                                    │
// │  Installation :                                                    │
// │  1. App Store → installer "Scriptable" (gratuit)                  │
// │  2. Ouvre Scriptable → bouton "+" → colle ce script                │
// │  3. Nomme-le "Trading Bot"                                         │
// │  4. Sur l'écran d'accueil : appui long → "+"                       │
// │  5. Cherche "Scriptable" → choisir "Medium" ou "Large"             │
// │  6. Ajouter → appui long sur le widget → "Edit"                    │
// │  7. Script: "Trading Bot" → Done                                   │
// └──────────────────────────────────────────────────────────────────┘

const REPO = "emirsqalli23-cpu/trading-bot";
const DASHBOARD_URL = `https://emirsqalli23-cpu.github.io/trading-bot/`;
const CAP_START = 1000;

// Couleurs
const COLOR_BG_TOP    = new Color("#1e3a8a");
const COLOR_BG_BOTTOM = new Color("#0f1419");
const COLOR_POS       = new Color("#22c55e");
const COLOR_NEG       = new Color("#ef4444");
const COLOR_NEU       = new Color("#94a3b8");
const COLOR_TEXT      = new Color("#e6e9ec");
const COLOR_LABEL     = new Color("#94a3b8");
const COLOR_CARD      = new Color("#1a2027");

const MARKETS = [
  { key: "crypto", emoji: "🪙", label: "Crypto" },
  { key: "gold",   emoji: "🥇", label: "Or" },
  { key: "forex",  emoji: "💱", label: "Forex" },
];

async function fetchState(market) {
  const url = `https://raw.githubusercontent.com/${REPO}/main/state/state_${market}.json?t=${Date.now()}`;
  try {
    const r = new Request(url);
    r.timeoutInterval = 10;
    return await r.loadJSON();
  } catch (e) {
    return null;
  }
}

function colorForPnl(pnl) {
  if (pnl > 0) return COLOR_POS;
  if (pnl < 0) return COLOR_NEG;
  return COLOR_NEU;
}

function fmt(n, sign = true) {
  return (n >= 0 && sign ? "+" : "") + Math.round(n);
}

function pct(n) {
  return ((n / CAP_START) * 100).toFixed(1) + "%";
}

async function buildWidget() {
  const widget = new ListWidget();
  widget.url = DASHBOARD_URL; // tap = ouvre le dashboard

  // Fond dégradé
  const grad = new LinearGradient();
  grad.colors = [COLOR_BG_TOP, COLOR_BG_BOTTOM];
  grad.locations = [0, 1];
  widget.backgroundGradient = grad;
  widget.setPadding(12, 14, 12, 14);

  // Récup tous les states en parallèle
  const states = await Promise.all(MARKETS.map(m => fetchState(m.key)));

  // Calcul total
  let totalCap = 0, totalPnl = 0, totalTrades = 0, totalWins = 0;
  let allOpen = 0;
  states.forEach(s => {
    if (s) {
      totalCap += s.capital;
      totalPnl += s.capital - CAP_START;
      const t = s.trades || [];
      totalTrades += t.length;
      totalWins += t.filter(x => x.pnl > 0).length;
      allOpen += Object.keys(s.positions || {}).length;
    } else {
      totalCap += CAP_START;
    }
  });
  const totalWr = totalTrades ? Math.round(100 * totalWins / totalTrades) : 0;

  const family = config.widgetFamily; // small | medium | large

  // ─── HEADER ─────────────────────────────────────────────
  const head = widget.addStack();
  head.layoutHorizontally();
  head.centerAlignContent();

  const titleTxt = head.addText("📊 Trading Bot");
  titleTxt.font = Font.boldSystemFont(13);
  titleTxt.textColor = COLOR_TEXT;

  head.addSpacer();

  const dot = head.addText("●");
  dot.font = Font.boldSystemFont(10);
  dot.textColor = COLOR_POS;

  widget.addSpacer(4);

  // ─── TOTAL CAPITAL ──────────────────────────────────────
  const capText = widget.addText(`${Math.round(totalCap)}$`);
  capText.font = Font.boldSystemFont(family === "small" ? 22 : 30);
  capText.textColor = colorForPnl(totalPnl);

  const pnlText = widget.addText(`${fmt(totalPnl)}$ · ${pct(totalPnl * 3)}`);
  pnlText.font = Font.systemFont(11);
  pnlText.textColor = colorForPnl(totalPnl);

  if (family !== "small") {
    widget.addSpacer(8);

    // ─── 3 BOTS HORIZONTAL ────────────────────────────────
    const row = widget.addStack();
    row.layoutHorizontally();
    row.spacing = 8;

    states.forEach((s, i) => {
      const m = MARKETS[i];
      const cap = s ? s.capital : CAP_START;
      const pnl = cap - CAP_START;
      const trades = s ? (s.trades || []) : [];
      const wins = trades.filter(t => t.pnl > 0).length;
      const wr = trades.length ? Math.round(100 * wins / trades.length) : 0;
      const open = s ? Object.keys(s.positions || {}).length : 0;

      const card = row.addStack();
      card.layoutVertically();
      card.backgroundColor = COLOR_CARD;
      card.cornerRadius = 8;
      card.setPadding(8, 8, 8, 8);
      card.size = new Size(0, 0);

      const t = card.addText(`${m.emoji} ${m.label}`);
      t.font = Font.boldSystemFont(10);
      t.textColor = COLOR_LABEL;

      const c = card.addText(`${Math.round(cap)}$`);
      c.font = Font.boldSystemFont(15);
      c.textColor = colorForPnl(pnl);

      const p = card.addText(`${fmt(pnl)}$`);
      p.font = Font.systemFont(10);
      p.textColor = colorForPnl(pnl);

      if (family === "large") {
        card.addSpacer(2);
        const tr = card.addText(`${trades.length} trades · ${wr}%`);
        tr.font = Font.systemFont(9);
        tr.textColor = COLOR_LABEL;
        if (open > 0) {
          const op = card.addText(`📂 ${open} ouverte${open > 1 ? "s" : ""}`);
          op.font = Font.systemFont(9);
          op.textColor = new Color("#fdba74");
        }
      }
    });
  }

  // ─── PIPELINE EN BAS (large widget seulement) ──────────
  if (family === "large") {
    widget.addSpacer(10);

    // Trouver le bot avec le cycle le plus récent
    let lastCycle = null, lastMarket = null;
    states.forEach((s, i) => {
      if (s && s.last_cycle && (!lastCycle || s.last_cycle.time > lastCycle.time)) {
        lastCycle = s.last_cycle;
        lastMarket = MARKETS[i];
      }
    });

    if (lastCycle) {
      const t = widget.addText(`🤖 ${lastMarket.label} — il y a ${howLongAgo(lastCycle.time)}`);
      t.font = Font.boldSystemFont(10);
      t.textColor = COLOR_LABEL;

      // Status
      let statusLine = "";
      if (lastCycle.status === "WAIT_KILLZONE") statusLine = "💤 Hors killzone — en veille";
      else if (lastCycle.status === "DAILY_LOSS_LOCK") statusLine = "🛑 Trading suspendu (limite -3%)";
      else if (lastCycle.actions?.length) statusLine = `⚡ ${lastCycle.actions[0]}`;
      else if (lastCycle.symbols?.length) {
        const waiting = lastCycle.symbols.filter(s => s.decision === "WAIT_MSS").length;
        if (waiting) statusLine = `⏳ Attend MSS sur ${waiting} symbole${waiting > 1 ? "s" : ""}`;
        else statusLine = `🔍 Analyse en cours`;
      } else {
        statusLine = "🔍 Analyse en cours";
      }
      const sl = widget.addText(statusLine);
      sl.font = Font.systemFont(11);
      sl.textColor = COLOR_TEXT;
    }
  }

  widget.addSpacer();

  // ─── FOOTER ─────────────────────────────────────────────
  const foot = widget.addStack();
  foot.layoutHorizontally();

  const stats = foot.addText(`${totalTrades} trades · WR ${totalWr}% · ${allOpen} ouvertes`);
  stats.font = Font.systemFont(9);
  stats.textColor = COLOR_LABEL;

  foot.addSpacer();

  const time = new Date().toLocaleTimeString("fr-FR", {hour: "2-digit", minute: "2-digit"});
  const ts = foot.addText(time);
  ts.font = Font.systemFont(9);
  ts.textColor = COLOR_LABEL;

  return widget;
}

function howLongAgo(iso) {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60)    return Math.floor(diff) + "s";
  if (diff < 3600)  return Math.floor(diff/60) + "min";
  if (diff < 86400) return Math.floor(diff/3600) + "h";
  return Math.floor(diff/86400) + "j";
}

// ─── MAIN ─────────────────────────────────────────────────
const widget = await buildWidget();

if (config.runsInWidget) {
  Script.setWidget(widget);
} else {
  // Preview en mode app
  await widget.presentLarge();
}
Script.complete();
