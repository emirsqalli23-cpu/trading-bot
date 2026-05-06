// ┌──────────────────────────────────────────────────────────────────┐
// │  Trading Bot Widget v2 — Scriptable iOS                          │
// │  Capital + statut + dernière action + macro + sentiment          │
// │                                                                    │
// │  Installation :                                                    │
// │  1. App Store → installer "Scriptable" (gratuit)                  │
// │  2. Ouvre Scriptable → bouton "+" → colle ce script                │
// │  3. Nomme-le "Trading Bot"                                         │
// │  4. Sur l'écran d'accueil : appui long → "+"                       │
// │  5. Cherche "Scriptable" → choisir taille "Medium" ou "Large"      │
// │  6. Tape le widget → Edit → Script: "Trading Bot" → Done           │
// └──────────────────────────────────────────────────────────────────┘

const REPO = "emirsqalli23-cpu/trading-bot";
const DASHBOARD_URL = `https://emirsqalli23-cpu.github.io/trading-bot/`;
const CAP_START = 1000;

// ═══ Couleurs ═══
const C_BG_TOP    = new Color("#1e3a8a");
const C_BG_BOT    = new Color("#0a0e14");
const C_POS       = new Color("#22c55e");
const C_NEG       = new Color("#ef4444");
const C_NEU       = new Color("#94a3b8");
const C_TEXT      = new Color("#e6e9ec");
const C_LABEL     = new Color("#94a3b8");
const C_CARD      = new Color("#1a2027");
const C_GOLD      = new Color("#fbbf24");
const C_PURPLE    = new Color("#a78bfa");
const C_ORANGE    = new Color("#fb923c");

const MARKETS = [
  { key: "crypto", emoji: "🪙", label: "Crypto" },
  { key: "gold",   emoji: "🥇", label: "Or" },
  { key: "forex",  emoji: "💱", label: "Forex" },
];

// ═══ Helpers fetch ═══
async function fetchState(market) {
  const url = `https://raw.githubusercontent.com/${REPO}/main/state/state_${market}.json?t=${Date.now()}`;
  try {
    const r = new Request(url);
    r.timeoutInterval = 10;
    return await r.loadJSON();
  } catch (e) { return null; }
}

async function fetchFearGreed() {
  try {
    const r = new Request("https://api.alternative.me/fng/?limit=1");
    r.timeoutInterval = 8;
    const d = await r.loadJSON();
    return { value: parseInt(d.data[0].value), label: d.data[0].value_classification };
  } catch { return null; }
}

// ═══ Helpers format ═══
function colorForPnl(pnl) {
  if (pnl > 0.5)  return C_POS;
  if (pnl < -0.5) return C_NEG;
  return C_NEU;
}

function fmt(n, sign = true) {
  return (n >= 0 && sign ? "+" : "") + Math.round(n);
}

function pct(n) {
  return ((n / CAP_START) * 100).toFixed(1) + "%";
}

function howAgo(iso) {
  if (!iso) return "?";
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60)    return Math.floor(diff) + "s";
  if (diff < 3600)  return Math.floor(diff/60) + "m";
  if (diff < 86400) return Math.floor(diff/3600) + "h";
  return Math.floor(diff/86400) + "j";
}

function fearGreedEmoji(v) {
  if (v == null) return "❓";
  if (v <= 20)   return "💎";   // extreme fear (opportunité achat)
  if (v <= 44)   return "😰";   // fear
  if (v <= 55)   return "😐";   // neutral
  if (v <= 74)   return "😎";   // greed
  return "🤑";                   // extreme greed
}

// ═══ Widget builder ═══
async function buildWidget() {
  const widget = new ListWidget();
  widget.url = DASHBOARD_URL;

  // Fond dégradé
  const grad = new LinearGradient();
  grad.colors = [C_BG_TOP, C_BG_BOT];
  grad.locations = [0, 1];
  widget.backgroundGradient = grad;
  widget.setPadding(12, 14, 12, 14);

  // Charge tout en parallèle
  const [states, fg] = await Promise.all([
    Promise.all(MARKETS.map(m => fetchState(m.key))),
    fetchFearGreed(),
  ]);

  // Calcul total
  let totalCap = 0, totalPnl = 0, totalTrades = 0, totalWins = 0;
  let allOpen = 0;
  let lastTrade = null;
  let lastCycleTime = null;
  let blockedCount = 0;
  states.forEach((s, i) => {
    if (s) {
      totalCap += s.capital;
      totalPnl += s.capital - CAP_START;
      const t = s.trades || [];
      totalTrades += t.length;
      totalWins += t.filter(x => x.pnl > 0).length;
      allOpen += Object.keys(s.positions || {}).length;
      // Trade le plus récent toutes catégories
      t.forEach(tr => {
        if (!lastTrade || tr.time > lastTrade.time) lastTrade = { ...tr, market: MARKETS[i].label };
      });
      // Dernier cycle
      const lc = s.last_cycle;
      if (lc?.time && (!lastCycleTime || lc.time > lastCycleTime)) lastCycleTime = lc.time;
      // Compte blocages
      (lc?.symbols || []).forEach(x => {
        if ((x.decision || "").startsWith("BLOCKED")) blockedCount++;
      });
    } else {
      totalCap += CAP_START;
    }
  });
  const totalWr = totalTrades ? Math.round(100 * totalWins / totalTrades) : 0;
  const family = config.widgetFamily;

  // ═══ HEADER ═══
  const head = widget.addStack();
  head.layoutHorizontally();
  head.centerAlignContent();
  const titleTxt = head.addText("📊 Trading Bot");
  titleTxt.font = Font.boldSystemFont(13);
  titleTxt.textColor = C_TEXT;
  head.addSpacer();
  // Statut "live" si cycle récent (< 15min)
  const isLive = lastCycleTime && (Date.now() - new Date(lastCycleTime).getTime()) < 900000;
  const dot = head.addText(isLive ? "● LIVE" : "● IDLE");
  dot.font = Font.boldSystemFont(9);
  dot.textColor = isLive ? C_POS : C_NEU;

  widget.addSpacer(4);

  // ═══ TOTAL CAPITAL ═══
  const capText = widget.addText(`${Math.round(totalCap)}€`);
  capText.font = Font.boldSystemFont(family === "small" ? 22 : 32);
  capText.textColor = colorForPnl(totalPnl);

  const pnlRow = widget.addStack();
  pnlRow.layoutHorizontally();
  const pnlText = pnlRow.addText(`${fmt(totalPnl)}€ · ${pct(totalPnl * 3)}`);
  pnlText.font = Font.systemFont(11);
  pnlText.textColor = colorForPnl(totalPnl);
  pnlRow.addSpacer();
  // Stats globaux
  const wrText = pnlRow.addText(`WR ${totalWr}% · ${totalTrades}T`);
  wrText.font = Font.systemFont(10);
  wrText.textColor = C_LABEL;

  if (family !== "small") {
    widget.addSpacer(8);

    // ═══ 3 BOTS HORIZONTAL ═══
    const row = widget.addStack();
    row.layoutHorizontally();
    row.spacing = 6;

    states.forEach((s, i) => {
      const m = MARKETS[i];
      const cap = s ? s.capital : CAP_START;
      const pnl = cap - CAP_START;
      const trades = s ? (s.trades || []) : [];
      const wins = trades.filter(t => t.pnl > 0).length;
      const wr = trades.length ? Math.round(100 * wins / trades.length) : 0;
      const open = s ? Object.keys(s.positions || {}).length : 0;
      const lc = s?.last_cycle;
      const status = lc?.status || "?";

      const card = row.addStack();
      card.layoutVertically();
      card.backgroundColor = C_CARD;
      card.cornerRadius = 8;
      card.setPadding(7, 7, 7, 7);
      card.size = new Size(0, 0);

      const header = card.addStack();
      header.layoutHorizontally();
      header.centerAlignContent();
      const t = header.addText(`${m.emoji}`);
      t.font = Font.systemFont(11);
      header.addSpacer(2);
      const tl = header.addText(m.label);
      tl.font = Font.boldSystemFont(9);
      tl.textColor = C_LABEL;

      const c = card.addText(`${Math.round(cap)}€`);
      c.font = Font.boldSystemFont(14);
      c.textColor = colorForPnl(pnl);

      const p = card.addText(`${fmt(pnl)}€`);
      p.font = Font.systemFont(9);
      p.textColor = colorForPnl(pnl);

      if (family === "large") {
        card.addSpacer(2);
        const tr = card.addText(`${trades.length}T · ${wr}%`);
        tr.font = Font.systemFont(8);
        tr.textColor = C_LABEL;

        // Statut visuel
        let statusIcon = "💤";
        if (status === "WAIT_KILLZONE") statusIcon = "⏰";
        else if (status === "DAILY_LOSS_LOCK") statusIcon = "🛑";
        else if (status === "ANALYZED") statusIcon = "🔍";
        else if (open > 0) statusIcon = "🚀";

        const st = card.addText(`${statusIcon} ${open > 0 ? open + ' pos' : '0 pos'}`);
        st.font = Font.systemFont(8);
        st.textColor = open > 0 ? C_GOLD : C_LABEL;
      }
    });
  }

  // ═══ DERNIÈRE ACTION + MACRO (large widget seulement) ═══
  if (family === "large") {
    widget.addSpacer(8);

    // Dernière action
    const actionCard = widget.addStack();
    actionCard.layoutVertically();
    actionCard.backgroundColor = C_CARD;
    actionCard.cornerRadius = 8;
    actionCard.setPadding(8, 10, 8, 10);

    const actionLabel = actionCard.addText("⚡ DERNIÈRE ACTION");
    actionLabel.font = Font.boldSystemFont(9);
    actionLabel.textColor = C_LABEL;

    if (lastTrade) {
      const sign = lastTrade.pnl >= 0 ? "+" : "";
      const typeMap = {
        "TP": "🎯 TP touché", "TP_PARTIAL": "💰 50% pris",
        "TP_EXTENDED": "🎯 Objectif final", "TRAIL_EXIT": "🛡️ Trailing",
        "SL": "❌ SL", "BE": "⚪ Pari nul",
        "SHOCK_EXIT": "🚨 Sortie news", "TRUMP_SHOCK_EXIT": "🚨 Trump shock",
      };
      const typeStr = typeMap[lastTrade.type] || lastTrade.type;
      const at = actionCard.addText(`${typeStr} sur ${lastTrade.symbol}`);
      at.font = Font.boldSystemFont(11);
      at.textColor = C_TEXT;
      const ap = actionCard.addText(`${sign}${Math.round(lastTrade.pnl)}€ — il y a ${howAgo(lastTrade.time)} (${lastTrade.market})`);
      ap.font = Font.systemFont(9);
      ap.textColor = colorForPnl(lastTrade.pnl);
    } else {
      const at = actionCard.addText("Aucun trade encore");
      at.font = Font.systemFont(11);
      at.textColor = C_LABEL;
    }

    widget.addSpacer(6);

    // Macro context
    const macroRow = widget.addStack();
    macroRow.layoutHorizontally();
    macroRow.spacing = 6;

    // Fear & Greed
    const fgCard = macroRow.addStack();
    fgCard.layoutVertically();
    fgCard.backgroundColor = C_CARD;
    fgCard.cornerRadius = 6;
    fgCard.setPadding(5, 7, 5, 7);
    const fgEmoji = fearGreedEmoji(fg?.value);
    const fgLabel = fgCard.addText(`${fgEmoji} F&G crypto`);
    fgLabel.font = Font.boldSystemFont(8);
    fgLabel.textColor = C_LABEL;
    const fgVal = fgCard.addText(fg?.value != null ? `${fg.value}/100` : "?");
    fgVal.font = Font.boldSystemFont(11);
    fgVal.textColor = C_TEXT;
    const fgL = fgCard.addText(fg?.label || "");
    fgL.font = Font.systemFont(8);
    fgL.textColor = C_LABEL;

    // Macro 2 — derniers checks bot
    const cryptoState = states[0]; // crypto a souvent le cycle le + récent
    const lc = states.find(s => s?.last_cycle)?.last_cycle;
    const macroCard = macroRow.addStack();
    macroCard.layoutVertically();
    macroCard.backgroundColor = C_CARD;
    macroCard.cornerRadius = 6;
    macroCard.setPadding(5, 7, 5, 7);
    const macroL = macroCard.addText("🌍 Macro");
    macroL.font = Font.boldSystemFont(8);
    macroL.textColor = C_LABEL;
    const dxyT = lc?.checks?.dxy?.trend || "?";
    const yieldsT = lc?.checks?.yields_10y?.trend || "?";
    const dxyTxt = macroCard.addText(`DXY ${dxyT === "BULLISH" ? "↑" : dxyT === "BEARISH" ? "↓" : "─"}`);
    dxyTxt.font = Font.systemFont(9);
    dxyTxt.textColor = C_TEXT;
    const yTxt = macroCard.addText(`Yields ${yieldsT === "BULLISH" ? "↑" : yieldsT === "BEARISH" ? "↓" : "─"}`);
    yTxt.font = Font.systemFont(9);
    yTxt.textColor = C_TEXT;

    // Killzone
    const kzCard = macroRow.addStack();
    kzCard.layoutVertically();
    kzCard.backgroundColor = C_CARD;
    kzCard.cornerRadius = 6;
    kzCard.setPadding(5, 7, 5, 7);
    const kzL = kzCard.addText("⏰ Killzone");
    kzL.font = Font.boldSystemFont(8);
    kzL.textColor = C_LABEL;
    const kzActive = lc?.checks?.killzone?.ok;
    const kzName = lc?.checks?.killzone?.name || "Hors zone";
    const kzS = kzCard.addText(kzActive ? "Active" : "Off");
    kzS.font = Font.boldSystemFont(11);
    kzS.textColor = kzActive ? C_POS : C_NEU;
    const kzN = kzCard.addText(kzName.split(" ")[0] + (kzName.split(" ")[1] ? " " + kzName.split(" ")[1] : ""));
    kzN.font = Font.systemFont(8);
    kzN.textColor = C_LABEL;
  }

  widget.addSpacer();

  // ═══ FOOTER ═══
  const foot = widget.addStack();
  foot.layoutHorizontally();
  const stats = foot.addText(`${allOpen} pos · ${blockedCount} blocs`);
  stats.font = Font.systemFont(9);
  stats.textColor = C_LABEL;
  foot.addSpacer();
  const time = new Date().toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  const ts = foot.addText(time);
  ts.font = Font.systemFont(9);
  ts.textColor = C_LABEL;

  return widget;
}

// ═══ MAIN ═══
const widget = await buildWidget();
if (config.runsInWidget) {
  Script.setWidget(widget);
} else {
  await widget.presentLarge();
}
Script.complete();
