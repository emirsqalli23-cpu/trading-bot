// ╔══════════════════════════════════════════════════════════════════╗
// ║  TRADING BOT — Widget Scriptable iOS (v2 avec cache)             ║
// ║  Capital + statut + tap → Dashboard + Chat IA                    ║
// ║                                                                    ║
// ║  Si le widget timeout, c'est que GitHub raw rame.                 ║
// ║  Cette version garde un cache local : même si le fetch échoue,    ║
// ║  le widget affiche les dernières données connues.                 ║
// ║                                                                    ║
// ║  INSTALLATION                                                      ║
// ║  1. Scriptable → "+" → Coller ce code → Renommer "Trading Bot"     ║
// ║  2. Écran d'accueil → appui long → "+" → Scriptable                ║
// ║     → taille MEDIUM ou LARGE → Ajouter                             ║
// ║  3. Tap widget → "Edit Widget" → Script: "Trading Bot" → Done     ║
// ╚══════════════════════════════════════════════════════════════════╝

const REPO          = "emirsqalli23-cpu/trading-bot";
const DASHBOARD_URL = "https://emirsqalli23-cpu.github.io/trading-bot/";
const CAP_START     = 1000;
const TIMEOUT_S     = 6;       // 6s max par requête (sinon cache)

// ═══ COULEURS ═══
const C_BG_TOP = new Color("#1e3a8a");
const C_BG_BOT = new Color("#0a0e14");
const C_POS    = new Color("#22c55e");
const C_NEG    = new Color("#ef4444");
const C_NEU    = new Color("#94a3b8");
const C_TEXT   = new Color("#e6e9ec");
const C_LABEL  = new Color("#94a3b8");
const C_CARD   = new Color("#1a2027");
const C_GOLD   = new Color("#fbbf24");

const MARKETS = [
  { key: "crypto", emoji: "🪙", label: "Crypto" },
  { key: "gold",   emoji: "🥇", label: "Or" },
  { key: "forex",  emoji: "💱", label: "Forex" },
];

// ═══ CACHE LOCAL (FileManager) ═══
const fm = FileManager.local();
const CACHE_DIR = fm.joinPath(fm.documentsDirectory(), "tradingbot-cache");
if (!fm.fileExists(CACHE_DIR)) fm.createDirectory(CACHE_DIR, true);

function cacheRead(name) {
  const p = fm.joinPath(CACHE_DIR, name + ".json");
  if (!fm.fileExists(p)) return null;
  try { return JSON.parse(fm.readString(p)); } catch { return null; }
}
function cacheWrite(name, data) {
  try {
    const p = fm.joinPath(CACHE_DIR, name + ".json");
    fm.writeString(p, JSON.stringify(data));
  } catch {}
}

// ═══ FETCH avec fallback cache ═══
async function fetchStateCached(market) {
  try {
    const url = `https://raw.githubusercontent.com/${REPO}/main/state/state_${market}.json?t=${Date.now()}`;
    const r = new Request(url);
    r.timeoutInterval = TIMEOUT_S;
    const data = await r.loadJSON();
    cacheWrite("state_" + market, data);
    return data;
  } catch {
    return cacheRead("state_" + market);
  }
}

async function fetchFearGreedCached() {
  try {
    const r = new Request("https://api.alternative.me/fng/?limit=1");
    r.timeoutInterval = 5;
    const d = await r.loadJSON();
    const out = { value: parseInt(d.data[0].value), label: d.data[0].value_classification };
    cacheWrite("fng", out);
    return out;
  } catch {
    return cacheRead("fng");
  }
}

// ═══ HELPERS ═══
function colorForPnl(p) { return p > 0.5 ? C_POS : p < -0.5 ? C_NEG : C_NEU; }
function fmtN(n) { return (n >= 0 ? "+" : "") + Math.round(n); }
function pct(n) { return ((n / CAP_START) * 100).toFixed(1) + "%"; }
function howAgo(iso) {
  if (!iso) return "?";
  const d = (Date.now() - new Date(iso).getTime()) / 1000;
  if (d < 60)    return Math.floor(d) + "s";
  if (d < 3600)  return Math.floor(d / 60) + "m";
  if (d < 86400) return Math.floor(d / 3600) + "h";
  return Math.floor(d / 86400) + "j";
}
function fgEmoji(v) {
  if (v == null) return "❓";
  if (v <= 20) return "💎";
  if (v <= 44) return "😰";
  if (v <= 55) return "😐";
  if (v <= 74) return "😎";
  return "🤑";
}

// ═══ WIDGET FALLBACK (en cas d'erreur totale) ═══
function buildErrorWidget(msg) {
  const w = new ListWidget();
  w.url = DASHBOARD_URL;
  const grad = new LinearGradient();
  grad.colors = [C_BG_TOP, C_BG_BOT];
  grad.locations = [0, 1];
  w.backgroundGradient = grad;
  w.setPadding(14, 14, 14, 14);

  const t = w.addText("🤖 Trading Bot");
  t.font = Font.boldSystemFont(14);
  t.textColor = C_TEXT;
  w.addSpacer(8);
  const e = w.addText("⚠️ " + msg);
  e.font = Font.systemFont(11);
  e.textColor = C_NEU;
  w.addSpacer();
  const cta = w.addText("💬 Tap → Dashboard");
  cta.font = Font.boldSystemFont(11);
  cta.textColor = C_GOLD;
  return w;
}

// ═══ WIDGET PRINCIPAL ═══
async function buildWidget() {
  const widget = new ListWidget();
  widget.url = DASHBOARD_URL;

  const grad = new LinearGradient();
  grad.colors = [C_BG_TOP, C_BG_BOT];
  grad.locations = [0, 1];
  widget.backgroundGradient = grad;
  widget.setPadding(12, 14, 12, 14);

  // Charger en parallèle (chaque fetch a son propre timeout court + cache)
  const [states, fg] = await Promise.all([
    Promise.all(MARKETS.map(m => fetchStateCached(m.key))),
    fetchFearGreedCached(),
  ]);

  // Calculs globaux
  let totalCap = 0, totalPnl = 0, totalTrades = 0, totalWins = 0;
  let allOpen = 0, lastTrade = null, lastCycleTime = null;
  states.forEach((s, i) => {
    if (s) {
      totalCap += s.capital;
      totalPnl += s.capital - CAP_START;
      const t = s.trades || [];
      totalTrades += t.length;
      totalWins += t.filter(x => x.pnl > 0).length;
      allOpen += Object.keys(s.positions || {}).length;
      t.forEach(tr => {
        if (!lastTrade || tr.time > lastTrade.time)
          lastTrade = { ...tr, market: MARKETS[i].label };
      });
      const lc = s.last_cycle;
      if (lc?.time && (!lastCycleTime || lc.time > lastCycleTime)) lastCycleTime = lc.time;
    } else {
      totalCap += CAP_START;
    }
  });
  const totalWr = totalTrades ? Math.round(100 * totalWins / totalTrades) : 0;
  const family = config.widgetFamily || "medium";

  // ═══ HEADER ═══
  const head = widget.addStack();
  head.layoutHorizontally();
  head.centerAlignContent();
  const ttl = head.addText("🤖 Trading Bot");
  ttl.font = Font.boldSystemFont(13);
  ttl.textColor = C_TEXT;
  head.addSpacer();
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
  const pnlText = pnlRow.addText(`${fmtN(totalPnl)}€ · ${pct(totalPnl * 3)}`);
  pnlText.font = Font.systemFont(11);
  pnlText.textColor = colorForPnl(totalPnl);
  pnlRow.addSpacer();
  const wrText = pnlRow.addText(`WR ${totalWr}% · ${totalTrades}T`);
  wrText.font = Font.systemFont(10);
  wrText.textColor = C_LABEL;

  // ═══ MEDIUM/LARGE : 3 cartes ═══
  if (family !== "small") {
    widget.addSpacer(8);
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
      const status = s?.last_cycle?.status || "?";

      const card = row.addStack();
      card.layoutVertically();
      card.backgroundColor = C_CARD;
      card.cornerRadius = 8;
      card.setPadding(7, 7, 7, 7);

      const h = card.addStack();
      h.layoutHorizontally();
      h.centerAlignContent();
      const e = h.addText(m.emoji);
      e.font = Font.systemFont(11);
      h.addSpacer(2);
      const lb = h.addText(m.label);
      lb.font = Font.boldSystemFont(9);
      lb.textColor = C_LABEL;

      const c = card.addText(`${Math.round(cap)}€`);
      c.font = Font.boldSystemFont(14);
      c.textColor = colorForPnl(pnl);

      const p = card.addText(`${fmtN(pnl)}€`);
      p.font = Font.systemFont(9);
      p.textColor = colorForPnl(pnl);

      if (family === "large") {
        card.addSpacer(2);
        const tr = card.addText(`${trades.length}T · ${wr}%`);
        tr.font = Font.systemFont(8);
        tr.textColor = C_LABEL;

        let icon = "💤";
        if (status === "WAIT_KILLZONE")    icon = "⏰";
        else if (status === "DAILY_LOSS_LOCK") icon = "🛑";
        else if (status === "ANALYZED")    icon = "🔍";
        else if (open > 0)                 icon = "🚀";

        const st = card.addText(`${icon} ${open > 0 ? open + " pos" : "0"}`);
        st.font = Font.systemFont(8);
        st.textColor = open > 0 ? C_GOLD : C_LABEL;
      }
    });
  }

  // ═══ LARGE : dernière action + macro ═══
  if (family === "large") {
    widget.addSpacer(8);
    const actCard = widget.addStack();
    actCard.layoutVertically();
    actCard.backgroundColor = C_CARD;
    actCard.cornerRadius = 8;
    actCard.setPadding(8, 10, 8, 10);

    const aLbl = actCard.addText("⚡ DERNIÈRE ACTION");
    aLbl.font = Font.boldSystemFont(9);
    aLbl.textColor = C_LABEL;

    if (lastTrade) {
      const sign = lastTrade.pnl >= 0 ? "+" : "";
      const map = {
        "TP": "🎯 TP", "TP_PARTIAL": "💰 50%",
        "TP_EXTENDED": "🎯 Final", "TRAIL_EXIT": "🛡️ Trail",
        "SL": "❌ SL", "BE": "⚪ Nul", "SHOCK_EXIT": "🚨 News",
      };
      const tStr = map[lastTrade.type] || lastTrade.type;
      const at = actCard.addText(`${tStr} sur ${lastTrade.symbol}`);
      at.font = Font.boldSystemFont(11);
      at.textColor = C_TEXT;
      const ap = actCard.addText(`${sign}${Math.round(lastTrade.pnl)}€ — ${howAgo(lastTrade.time)}`);
      ap.font = Font.systemFont(9);
      ap.textColor = colorForPnl(lastTrade.pnl);
    } else {
      const at = actCard.addText("Aucun trade encore");
      at.font = Font.systemFont(11);
      at.textColor = C_LABEL;
    }

    widget.addSpacer(6);

    const macroRow = widget.addStack();
    macroRow.layoutHorizontally();
    macroRow.spacing = 6;

    // Fear & Greed
    const fgC = macroRow.addStack();
    fgC.layoutVertically();
    fgC.backgroundColor = C_CARD;
    fgC.cornerRadius = 6;
    fgC.setPadding(5, 7, 5, 7);
    const fgL = fgC.addText(`${fgEmoji(fg?.value)} F&G`);
    fgL.font = Font.boldSystemFont(8);
    fgL.textColor = C_LABEL;
    const fgV = fgC.addText(fg?.value != null ? `${fg.value}/100` : "?");
    fgV.font = Font.boldSystemFont(11);
    fgV.textColor = C_TEXT;
    const fgT = fgC.addText(fg?.label || "");
    fgT.font = Font.systemFont(8);
    fgT.textColor = C_LABEL;

    // Macro
    const lc = states.find(s => s?.last_cycle)?.last_cycle;
    const mC = macroRow.addStack();
    mC.layoutVertically();
    mC.backgroundColor = C_CARD;
    mC.cornerRadius = 6;
    mC.setPadding(5, 7, 5, 7);
    const mL = mC.addText("🌍 Macro");
    mL.font = Font.boldSystemFont(8);
    mL.textColor = C_LABEL;
    const arr = t => t === "BULLISH" ? "↑" : t === "BEARISH" ? "↓" : "─";
    const dT = mC.addText(`DXY ${arr(lc?.checks?.dxy?.trend)}`);
    dT.font = Font.systemFont(9);
    dT.textColor = C_TEXT;
    const yT = mC.addText(`Yld ${arr(lc?.checks?.yields_10y?.trend)}`);
    yT.font = Font.systemFont(9);
    yT.textColor = C_TEXT;

    // Killzone
    const kC = macroRow.addStack();
    kC.layoutVertically();
    kC.backgroundColor = C_CARD;
    kC.cornerRadius = 6;
    kC.setPadding(5, 7, 5, 7);
    const kL = kC.addText("⏰ Zone");
    kL.font = Font.boldSystemFont(8);
    kL.textColor = C_LABEL;
    const kAct = lc?.checks?.killzone?.ok;
    const kS = kC.addText(kAct ? "ON" : "OFF");
    kS.font = Font.boldSystemFont(11);
    kS.textColor = kAct ? C_POS : C_NEU;
    const kN = kC.addText((lc?.checks?.killzone?.name || "—").split(" ")[0]);
    kN.font = Font.systemFont(8);
    kN.textColor = C_LABEL;
  }

  widget.addSpacer();

  // ═══ FOOTER ═══
  const foot = widget.addStack();
  foot.layoutHorizontally();
  const stats = foot.addText(`💬 Tap → Chat IA · ${allOpen} pos`);
  stats.font = Font.boldSystemFont(9);
  stats.textColor = C_GOLD;
  foot.addSpacer();
  const time = new Date().toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  const ts = foot.addText(time);
  ts.font = Font.systemFont(9);
  ts.textColor = C_LABEL;

  return widget;
}

// ═══ MAIN ═══
let widget;
try {
  widget = await buildWidget();
} catch (e) {
  widget = buildErrorWidget(e.message || "Erreur");
}

if (config.runsInWidget) {
  Script.setWidget(widget);
} else {
  // Mode interactif (test depuis Scriptable) : preview + ouvre dashboard
  await widget.presentLarge();
  Safari.open(DASHBOARD_URL);
}
Script.complete();
