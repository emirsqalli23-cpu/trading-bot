// ┌──────────────────────────────────────────────────────────────────┐
// │  Trading Bot Chatbot — Scriptable iOS                            │
// │  Discute avec ton bot directement depuis ton iPhone              │
// │                                                                    │
// │  Installation :                                                    │
// │  1. Ouvre Scriptable → "+" → colle ce script                       │
// │  2. Nomme-le "Bot Chat"                                            │
// │  3. Sur l'écran d'accueil : appui long → "+"                       │
// │     → Scriptable → taille "Small" ou "Medium"                      │
// │  4. Edit le widget : Script "Bot Chat", When Interacting "Run     │
// │     Script" → Done                                                 │
// │  5. Tap sur le widget → ouvre le chat                              │
// └──────────────────────────────────────────────────────────────────┘

const REPO = "emirsqalli23-cpu/trading-bot";
const GEMINI_API_KEY = "AIzaSyBewLDvtuS4miaMDRRTr-dqsi708WkoPEg";
const GEMINI_MODEL   = "gemini-2.5-flash";

// ═══ Couleurs ═══
const C_BG_TOP   = new Color("#1e3a8a");
const C_BG_BOT   = new Color("#0a0e14");
const C_TEXT     = new Color("#e6e9ec");
const C_LABEL    = new Color("#94a3b8");
const C_GREEN    = new Color("#22c55e");
const C_GOLD     = new Color("#fbbf24");

// ═══ FETCH STATE pour contexte ═══
async function fetchAllStates() {
  const markets = ["forex", "gold", "crypto"];
  const out = {};
  await Promise.all(markets.map(async m => {
    try {
      const r = new Request(`https://raw.githubusercontent.com/${REPO}/main/state/state_${m}.json?t=${Date.now()}`);
      r.timeoutInterval = 10;
      out[m] = await r.loadJSON();
    } catch { out[m] = null; }
  }));
  return out;
}

async function fetchFearGreed() {
  try {
    const r = new Request("https://api.alternative.me/fng/?limit=1");
    r.timeoutInterval = 8;
    const d = await r.loadJSON();
    return { value: parseInt(d.data[0].value), label: d.data[0].value_classification };
  } catch { return null; }
}

// ═══ CONTEXT BUILDER (pour Gemini) ═══
function buildContext(states, fg) {
  let ctx = "ÉTAT ACTUEL DU BOT TRADING — données live extraites des fichiers state JSON :\n\n";

  for (const [market, s] of Object.entries(states)) {
    if (!s) { ctx += `${market} : indisponible\n`; continue; }
    const cap = s.capital;
    const pnl = cap - 1000;
    const trades = s.trades || [];
    const wins = trades.filter(t => t.pnl > 0).length;
    const wr = trades.length ? Math.round(100 * wins / trades.length) : 0;
    const pos = s.positions || {};

    ctx += `🔸 ${market.toUpperCase()}\n`;
    ctx += `   Capital : ${cap.toFixed(0)}€ (PnL ${pnl >= 0 ? '+' : ''}${pnl.toFixed(0)}€, soit ${(pnl/10).toFixed(1)}%)\n`;
    ctx += `   Trades clos : ${trades.length} | Win rate : ${wr}%\n`;
    ctx += `   Positions ouvertes : ${Object.keys(pos).length}\n`;

    for (const [sym, p] of Object.entries(pos)) {
      ctx += `      • ${sym} ${p.direction} entrée=${p.entry?.toFixed(4)} SL=${p.sl?.toFixed(4)} TP=${p.tp1?.toFixed(4)} `;
      ctx += `[BE:${p.be_set?'oui':'non'} TP1pris:${p.tp1_taken?'oui':'non'} trail:${p.trail_active?'oui':'non'}]\n`;
      if (p.open_time) ctx += `         Ouvert : ${p.open_time}\n`;
    }

    // 5 derniers trades
    const last5 = trades.slice(-5);
    if (last5.length) {
      ctx += `   Derniers trades :\n`;
      for (const t of last5) {
        ctx += `      ${t.time?.slice(0,16)} ${t.symbol} ${t.type} ${t.pnl >= 0 ? '+' : ''}${Math.round(t.pnl)}€\n`;
      }
    }

    // Dernier cycle bot
    const lc = s.last_cycle;
    if (lc) {
      ctx += `   Dernier cycle : ${lc.time?.slice(0,16)} → statut ${lc.status}\n`;
      const checks = lc.checks || {};
      if (checks.killzone)   ctx += `      Killzone : ${checks.killzone.ok ? 'active' : 'off'} (${checks.killzone.name || ''})\n`;
      if (checks.dxy)        ctx += `      DXY : ${checks.dxy.trend}\n`;
      if (checks.yields_10y) ctx += `      Yields 10Y US : ${checks.yields_10y.trend}\n`;
      if (lc.symbols && lc.symbols.length) {
        for (const x of lc.symbols) {
          ctx += `      [${x.symbol}] décision: ${x.decision}`;
          if (x.details && x.details.length) ctx += ` — ${x.details.join(' · ').slice(0,100)}`;
          ctx += `\n`;
        }
      }
    }
    ctx += "\n";
  }

  if (fg) ctx += `🌍 Crypto Fear & Greed : ${fg.value}/100 (${fg.label})\n\n`;

  return ctx;
}

// ═══ GEMINI API ═══
async function askGemini(systemContext, userMessage, history) {
  const contents = [];
  // Le contexte système est envoyé comme un échange initial user/model
  const sys = `Tu es l'assistant pédagogique du bot trading de Souhir Sqalli (alias Emir).

RÈGLES :
- Souhir n'est PAS trader pro — il apprend. Explique TOUS les termes techniques simplement entre parenthèses ou en italique.
- Réponds TOUJOURS en français.
- Sois CONCIS (max 6-8 lignes par réponse — c'est sur un iPhone).
- Utilise des emojis pour rendre clair (📊 💰 ✅ ❌ 🛡️ 🎯 📈 📉).
- Si une question est vague, propose 2-3 réponses possibles.
- Tu as accès aux données LIVE du bot (positions, trades, cycles).
- Si tu ne sais pas, dis-le simplement.

GLOSSAIRE rapide :
- LONG = acheter (parier que ça monte) | SHORT = vendre (parier que ça baisse)
- SL (Stop Loss) = sécurité automatique | TP (Take Profit) = objectif de gain
- Breakeven = SL remonté à l'entrée → ne peut plus perdre
- Trailing = SL qui suit le prix vers le haut
- MSS = signal d'entrée ICT (cassure de structure)
- Killzone = heures où les banques tradent activement

${systemContext}`;

  contents.push({ role: "user", parts: [{ text: sys }] });
  contents.push({ role: "model", parts: [{ text: "OK Souhir, je suis prêt ! Pose-moi tes questions 🤖" }] });

  for (const h of history) {
    contents.push({ role: h.role, parts: [{ text: h.text }] });
  }
  contents.push({ role: "user", parts: [{ text: userMessage }] });

  const url = `https://generativelanguage.googleapis.com/v1beta/models/${GEMINI_MODEL}:generateContent?key=${GEMINI_API_KEY}`;
  const req = new Request(url);
  req.method = "POST";
  req.headers = { "Content-Type": "application/json" };
  req.body = JSON.stringify({
    contents,
    generationConfig: { temperature: 0.5, maxOutputTokens: 1500 }
  });
  req.timeoutInterval = 30;

  const resp = await req.loadJSON();
  if (resp.error) return `❌ Erreur API : ${resp.error.message}`;
  const text = resp.candidates?.[0]?.content?.parts?.[0]?.text;
  return text || "(Pas de réponse)";
}

// ═══ INTERACTIVE CHAT (UITable plus joli qu'Alert) ═══
async function runChat() {
  const states = await fetchAllStates();
  const fg = await fetchFearGreed();
  const ctx = buildContext(states, fg);
  const history = [];

  // Suggestions de questions au démarrage
  const suggestions = [
    "Comment va mon bot ?",
    "Pourquoi pas de trade aujourd'hui ?",
    "Explique le dernier trade",
    "Quel est le contexte macro ?",
    "Mes positions ouvertes",
    "Conseil pour améliorer le bot ?",
  ];

  // Première question via Alert avec liste de suggestions
  let firstQ = null;
  const choiceAlert = new Alert();
  choiceAlert.title = "🤖 Bot Trading";
  choiceAlert.message = "Pose une question rapide ou écris la tienne :";
  for (const s of suggestions) choiceAlert.addAction(s);
  choiceAlert.addAction("✏️ Écrire ma question");
  choiceAlert.addCancelAction("Annuler");
  const idx = await choiceAlert.presentSheet();
  if (idx === -1) return;
  if (idx < suggestions.length) {
    firstQ = suggestions[idx];
  } else {
    // Custom input
    const inp = new Alert();
    inp.title = "💬 Ta question";
    inp.addTextField("Tape ta question...", "");
    inp.addAction("Envoyer");
    inp.addCancelAction("Annuler");
    if (await inp.presentAlert() === -1) return;
    firstQ = inp.textFieldValue(0);
    if (!firstQ.trim()) return;
  }

  // Boucle de chat
  let userMsg = firstQ;
  while (userMsg) {
    // Affiche "réflexion en cours" via UITable
    const t = new UITable();
    t.showSeparators = true;

    // Message user
    let row = new UITableRow();
    let cell = row.addText(userMsg);
    cell.titleColor = C_TEXT;
    cell.titleFont  = Font.boldSystemFont(13);
    row.height = 40;
    row.backgroundColor = new Color("#1e293b");
    t.addRow(row);

    // Header "thinking"
    row = new UITableRow();
    cell = row.addText("🤔 Réflexion...");
    cell.titleColor = C_LABEL;
    cell.titleFont = Font.italicSystemFont(12);
    t.addRow(row);
    await t.present(false);

    // Vrai appel API
    let answer;
    try { answer = await askGemini(ctx, userMsg, history); }
    catch (e) { answer = "❌ Erreur : " + e.message; }

    history.push({ role: "user",  text: userMsg });
    history.push({ role: "model", text: answer });
    if (history.length > 16) history.splice(0, 4);

    // Affiche réponse
    const respAlert = new Alert();
    respAlert.title = "🤖 Réponse";
    respAlert.message = answer.length > 1500 ? answer.slice(0, 1500) + "…" : answer;
    respAlert.addAction("💬 Question suivante");
    respAlert.addAction("📋 Suggestions");
    respAlert.addCancelAction("Quitter");
    const action = await respAlert.presentAlert();
    if (action === -1) return;

    // Question suivante ou suggestions
    if (action === 1) {
      // Suggestions
      const sa = new Alert();
      sa.title = "📋 Suggestions";
      const sugs = [
        "Reformule autrement",
        "Donne-moi plus de détails",
        "Explique en plus simple",
        "Conseil concret ?",
        "C'est risqué ?",
        "Compare avec hier",
      ];
      for (const s of sugs) sa.addAction(s);
      sa.addAction("✏️ Écrire ma question");
      sa.addCancelAction("Quitter");
      const i2 = await sa.presentSheet();
      if (i2 === -1) return;
      if (i2 < sugs.length) {
        userMsg = sugs[i2];
      } else {
        const inp = new Alert();
        inp.title = "💬 Ta question";
        inp.addTextField("Tape ta question...", "");
        inp.addAction("Envoyer");
        inp.addCancelAction("Quitter");
        if (await inp.presentAlert() === -1) return;
        userMsg = inp.textFieldValue(0);
      }
    } else {
      const inp = new Alert();
      inp.title = "💬 Ta question";
      inp.addTextField("Tape ta question...", "");
      inp.addAction("Envoyer");
      inp.addCancelAction("Quitter");
      if (await inp.presentAlert() === -1) return;
      userMsg = inp.textFieldValue(0);
    }
    if (!userMsg || !userMsg.trim()) return;
  }
}

// ═══ WIDGET (cliquable pour ouvrir le chat) ═══
async function buildWidget() {
  const w = new ListWidget();
  const grad = new LinearGradient();
  grad.colors = [C_BG_TOP, C_BG_BOT];
  grad.locations = [0, 1];
  w.backgroundGradient = grad;
  w.setPadding(14, 14, 14, 14);

  // URL pour relancer le script en interactif au tap
  w.url = `scriptable:///run/${encodeURIComponent(Script.name())}`;

  const head = w.addStack();
  head.layoutHorizontally();
  head.centerAlignContent();
  const t1 = head.addText("🤖");
  t1.font = Font.systemFont(22);
  head.addSpacer(6);
  const t2 = head.addText("Bot Chat");
  t2.font = Font.boldSystemFont(15);
  t2.textColor = C_TEXT;

  w.addSpacer(8);

  // Statut rapide : capital total + dernier trade
  const states = await fetchAllStates();
  let totalCap = 0;
  let lastTrade = null;
  Object.values(states).forEach(s => {
    if (!s) return;
    totalCap += s.capital;
    (s.trades || []).forEach(t => {
      if (!lastTrade || t.time > lastTrade.time) lastTrade = t;
    });
  });

  const cap = w.addText(`${Math.round(totalCap)}€`);
  cap.font = Font.boldSystemFont(20);
  cap.textColor = totalCap >= 3000 ? C_GREEN : C_GOLD;

  if (lastTrade) {
    const sign = lastTrade.pnl >= 0 ? "+" : "";
    const lt = w.addText(`Last: ${sign}${Math.round(lastTrade.pnl)}€ ${lastTrade.symbol}`);
    lt.font = Font.systemFont(10);
    lt.textColor = C_LABEL;
  }

  w.addSpacer();

  const cta = w.addText("💬 Tap pour discuter");
  cta.font = Font.boldSystemFont(11);
  cta.textColor = C_GOLD;

  return w;
}

// ═══ MAIN ═══
if (config.runsInWidget) {
  const widget = await buildWidget();
  Script.setWidget(widget);
} else {
  await runChat();
}
Script.complete();
