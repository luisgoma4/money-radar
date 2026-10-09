/* money-radar · oráculo por voz (panel compartido por dashboard.html y espacio.html).
 *
 * OracleDock.init(host) solo muestra el panel si /api/health responde (oracle_server.py en local);
 * en GitHub Pages no hace nada. El host (cada página) aporta:
 *   host.page                 "dashboard" | "espacio"
 *   host.plan()               el plan actual (DATA.plan)
 *   host.view()               {tab?, focus?, path?}: lo que se está viendo ahora
 *   host.directView(view)     dirige la vista: {page?, tab?, mode?, focus:{kind,id}?, path?:[ids]}
 *   host.refresh()            recarga /api/state y repinta (tras una acción confirmada)
 *   host.onPhase(phaseId|null) opcional: resaltar la fase de la ceremonia
 * Voz: Web Speech API es-ES (Safari y Chrome). Toque = escucha hasta silencio; pulsación larga = pulsar para hablar.
 */
(function () {
  "use strict";
  const STORE = {
    get(k) { try { return JSON.parse(localStorage.getItem("money-radar:" + k)); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem("money-radar:" + k, JSON.stringify(v)); } catch (e) {} },
  };
  function el(tag, attrs, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") n.className = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) if (kid != null && kid !== false) n.append(kid);
    return n;
  }
  const norm = (s) => s.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[¿?¡!.,]/g, "").trim();
  async function api(path, body) {
    const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
    return r.json();
  }

  let H = null;
  const P = () => H.plan();
  const OD = { session: null, options: [], pending: null, lastSay: "", muted: !!STORE.get("od-muted"), listening: false, cer: null, busy: false };
  const $ = (id) => document.getElementById(id);

  function buildDock() {
    const dock = el("aside", { class: "oracle-dock", id: "oracle-dock", "aria-label": "Oráculo por voz" },
      el("header", { class: "od-head" },
        el("button", { type: "button", class: "od-title", id: "od-toggle", "aria-expanded": "true" }, "◉ Oráculo"),
        el("span", { class: "od-phase", id: "od-phase" }),
        el("button", { type: "button", class: "od-icon", id: "od-mute", title: "Silenciar la voz", "aria-pressed": "false" }, "🔊")),
      el("div", { class: "od-body", id: "od-body" },
        el("div", { class: "od-log", id: "od-log", "aria-live": "polite" }),
        el("div", { class: "od-confirm", id: "od-confirm", hidden: true },
          el("div", { id: "od-confirm-text" }),
          el("div", { class: "od-row" },
            el("button", { type: "button", class: "od-btn od-yes", id: "od-yes" }, "Sí, confirmar"),
            el("button", { type: "button", class: "od-btn", id: "od-no" }, "No"))),
        el("div", { class: "od-options", id: "od-options" }),
        el("div", { class: "od-interim", id: "od-interim" }),
        el("form", { class: "od-input", id: "od-form" },
          el("button", { type: "button", class: "od-mic", id: "od-mic",
            title: "Toca y habla (se detiene al callarte) · mantén pulsado para hablar mientras lo sujetas · también con la barra espaciadora" }, "🎙"),
          el("input", { id: "od-text", type: "text", autocomplete: "off", "aria-label": "Mensaje al oráculo",
            placeholder: H.page === "espacio" ? "«ve a Bancos», «sigue hacia ENISA», «camino de Arte a FECYT»…" : "Habla o escribe… («qué hago ahora», «empieza la ceremonia»)" }),
          el("button", { type: "submit", class: "od-btn" }, "↵"))));
    document.body.append(dock);
  }

  // ---- log and speech output ----
  let VOICE = null, TTS_UNLOCKED = false, PENDING_SPEECH = null;
  function pickVoice() {
    if (!("speechSynthesis" in window)) return;
    const vs = speechSynthesis.getVoices();
    VOICE = vs.find((x) => /^es[-_]ES$/i.test(x.lang) && /monica|mónica|paulina|google/i.test(x.name))
      || vs.find((x) => /^es[-_]ES$/i.test(x.lang)) || vs.find((x) => /^es/i.test(x.lang)) || null;
  }
  if ("speechSynthesis" in window) { pickVoice(); speechSynthesis.addEventListener?.("voiceschanged", pickVoice); }
  function speak(text) {
    if (OD.muted || !("speechSynthesis" in window) || !text) return;
    if (!TTS_UNLOCKED) { PENDING_SPEECH = text; return; }
    if (OD.listening) stopListening();
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text.replace(/[«»]/g, ""));
    u.lang = "es-ES"; if (VOICE) u.voice = VOICE; u.rate = 1.05;
    setTimeout(() => speechSynthesis.speak(u), 30);  // Safari drops an utterance queued right after cancel()
  }
  function unlockTTS() {
    if (TTS_UNLOCKED || !("speechSynthesis" in window)) return;
    TTS_UNLOCKED = true;
    const u = new SpeechSynthesisUtterance(" "); u.volume = 0; speechSynthesis.speak(u);  // inside the user gesture
    if (PENDING_SPEECH) { const s = PENDING_SPEECH; PENDING_SPEECH = null; setTimeout(() => speak(s), 150); }
  }
  function say(text, who = "or", src) {
    if (!text) return;
    $("od-log").append(el("div", { class: "od-msg " + who }, text, src ? el("span", { class: "src" }, src) : null));
    $("od-log").scrollTop = $("od-log").scrollHeight;
    if (who !== "or") return;
    OD.lastSay = text;
    speak(text);
  }
  function setOptions(opts) {
    OD.options = (opts || []).filter(Boolean);
    $("od-options").replaceChildren(...OD.options.map((o, i) => el("button", { type: "button", class: o.action ? "act" : "",
      title: o.action ? "Cambia el estado: pedirá confirmación" : "", onclick: () => choose(i) }, el("span", { class: "n" }, `${i + 1}.`), o.label)));
  }
  function thinking(on) { OD.busy = on; $("od-phase").classList.toggle("od-thinking", on); if (on) $("od-phase").textContent = "pensando"; else renderPhase(); }
  function renderPhase() {
    const ph = OD.cer ? P().oracle.phases[OD.cer.phase] : null;
    $("od-phase").textContent = ph ? `Ceremonia · ${OD.cer.phase + 1}/${P().oracle.phases.length} ${ph.name}` : "";
    if (H.onPhase) H.onPhase(ph ? ph.id : null);
  }
  function direct(v) {
    if (!v) return;
    if (v.page && v.page !== H.page) {
      const url = v.page === "espacio" ? "espacio.html" : "dashboard.html";
      const hash = v.focus && v.focus.id ? "#" + encodeURIComponent((v.focus.kind || "node") + ":" + v.focus.id) : "";
      setOptions([{ label: v.page === "espacio" ? "Abrir el Espacio 3D" : "Abrir el dashboard", run: () => window.open(url + hash, v.page) }, ...OD.options]);
      const w = window.open(url + hash, v.page);  // may be blocked outside a click (Safari): the option stays as fallback
      if (!w) say("Pulsa la opción para abrir la ventana.", "sys");
      return;
    }
    H.directView(v);
  }

  // ---- actions with confirmation ----
  async function propose(action) {
    const r = await api("/api/action", action);
    if (r.error) { say(r.error); return; }
    OD.pending = r.token;
    $("od-confirm-text").textContent = r.summary;
    $("od-confirm").hidden = false;
    say(r.summary + " ¿Confirmas?");
  }
  async function confirm(yes) {
    const token = OD.pending; OD.pending = null; $("od-confirm").hidden = true;
    if (!yes) { say("Cancelado. No he cambiado nada."); if (OD.cer && OD.cer.after) { const f = OD.cer.after; OD.cer.after = null; f(false); } return; }
    thinking(true);
    const r = await api("/api/action/confirm", { token });
    thinking(false);
    say(r.message || r.error || "Hecho.");
    if (r.ok) await H.refresh();
    if (OD.cer && OD.cer.after) { const f = OD.cer.after; OD.cer.after = null; f(!!r.ok); }
  }

  // ---- options and input ----
  function choose(i) {
    const o = OD.options[i]; if (!o) return;
    say(o.label, "me");
    if (o.ceremony) return ceremony(o.ceremony);
    if (o.run) return o.run();
    if (o.view) return direct(o.view);
    if (o.action) return propose(o.action);
    if (o.utterance) return converse(o.utterance);
  }
  const NUMS = { uno: 1, una: 1, primera: 1, primero: 1, dos: 2, segunda: 2, segundo: 2, tres: 3, tercera: 3, tercero: 3, cuatro: 4, cuarta: 4, cinco: 5, seis: 6 };
  async function handle(text) {
    text = text.trim(); if (!text) return;
    const t = norm(text);
    if (OD.pending) {
      if (/^(si|confirmo|confirmar|adelante|vale|de acuerdo|hazlo|ok|correcto)\b/.test(t)) { say(text, "me"); return confirm(true); }
      if (/^(no|cancela|cancelar|mejor no|para|detente)\b/.test(t)) { say(text, "me"); return confirm(false); }
    }
    const m = t.match(/^(?:opcion|la|el)\s+(\d+|uno|una|dos|tres|cuatro|cinco|seis|primera|primero|segunda|segundo|tercera|tercero|cuarta)$/) || t.match(/^(\d)$/);
    if (m && OD.options.length) { const n = /\d/.test(m[1]) ? +m[1] : NUMS[m[1]]; if (n) return choose(n - 1); }
    const byLabel = OD.options.findIndex((o) => norm(o.label) === t);
    if (byLabel >= 0) return choose(byLabel);
    if (/^(repite|repitelo|otra vez)$/.test(t)) { say(OD.lastSay); return; }
    if (/^(silencio|callate|calla|silencia)$/.test(t)) { toggleMute(true); return; }
    if (OD.cer && OD.cer.capture && OD.cer.capture !== "commitment") { say(text, "me"); const f = OD.cer.onCapture; OD.cer.capture = null; return f(text); }
    say(text, "me");
    return converse(text);
  }
  async function converse(text) {
    thinking(true);
    let r;
    const v = H.view() || {};
    try {
      r = await api("/api/converse", { text, context: { page: H.page, tab: v.tab, focus: v.focus, path: v.path, session: OD.session,
        ceremony: OD.cer ? { phase: P().oracle.phases[OD.cer.phase]?.id, capture: OD.cer.capture } : null } });
    } catch (e) { r = { say: "No puedo hablar con el servidor del oráculo." }; }
    thinking(false);
    if (r.session) OD.session = r.session;
    if (r.commitment && OD.cer) OD.cer.commitments.push(r.commitment);
    say(r.say, "or", r.source === "claude" ? "Claude" : null);
    setOptions(r.options);
    if (r.view) direct(r.view);
    if (r.ceremony) ceremony(r.ceremony);
    if (r.action) propose(r.action);
  }

  // ---- ceremony: state machine over plan.oracle.phases (views are generic; each host maps them) ----
  function ceremony(cmd) {
    if (cmd === "start") OD.cer = { phase: -1, face: 0, decisions: [], commitments: [], notes: [] };
    if (!OD.cer) return;
    if (cmd === "start" || cmd === "next") { OD.cer.phase++; OD.cer.capture = null; }
    if (cmd === "stop") { OD.cer = null; renderPhase(); say("Ceremonia interrumpida. Nada se ha registrado."); return; }
    renderPhase();
    const o = P().oracle, c = OD.cer, ph = o.phases[c.phase];
    const next = { label: "Siguiente fase", ceremony: "next" };
    if (!ph) { OD.cer = null; renderPhase(); return; }
    if (ph.id === "apertura") {
      direct({ tab: "oracle", focus: { kind: "phase", id: "apertura" } });
      const late = o.late.length ? ` Hay ${o.late.length} atrasos.` : "";
      say(`Abro la ceremonia. La nave en el horizonte es ${o.ship ? o.ship.name + ", grado " + o.ship.grade : "ninguna"}. El hito que más puertas abre es ${o.milestone ? o.milestone.name : "ninguno"}.${late} ¿Pasamos a leer el grafo?`);
      setOptions([next, o.ship ? { label: "Ver la nave", view: { tab: "radar", focus: { kind: "call", id: o.ship.id } } } : null,
        { label: "Ver el Gantt", view: { tab: "calendar", focus: { kind: "gantt" } } }, { label: "Terminar ceremonia", ceremony: "stop" }]);
    } else if (ph.id === "lectura") {
      direct({ tab: "graph", focus: { kind: "node", id: o.mediator ? o.mediator.id : "e_fund" } });
      say(`Por ${o.mediator ? o.mediator.label : "—"} pasa el mayor flujo de valor hacia las convocatorias. El confusor a vigilar es ${o.confounder ? o.confounder.label : "—"}, causa común de ${o.confounder ? o.confounder.calls : 0} convocatorias. ¿Profundizamos o seguimos?`);
      setOptions([next, o.confounder ? { label: "Ver el confusor", view: { tab: "graph", focus: { kind: "node", id: o.confounder.id } } } : null,
        { label: "Profundizar con el arquitecto causal", utterance: "Como arquitecto causal, analiza los mediadores y confusores clave del ecosistema y propón como máximo dos cambios de peso o relación, cada uno como opción con su acción." }]);
    } else if (ph.id === "ronda") {
      const q = o.questions[c.face];
      if (!q) { c.face = 0; return ceremony("next"); }
      direct({ tab: "graph", focus: { kind: "face", id: q.face } });
      say(`${q.name}: ${q.question}`);
      const advance = () => { c.face++; ceremony("same"); };
      setOptions([{ label: `Lo asume ${q.name}`, run: () => { c.notes.push(`${q.name} asume: ${q.question}`); say("Anotado."); advance(); } },
        { label: "Dictar respuesta", run: () => { c.capture = "answer"; c.onCapture = (txt) => { c.notes.push(`${q.name}: ${txt}`); say("Anotado."); advance(); }; say("Te escucho."); } },
        { label: "Pasar", run: advance }]);
    } else if (ph.id === "deliberacion") {
      direct({ tab: "graph", focus: { kind: "core" } });
      say("¿Cambiamos algún peso o relación? Como máximo dos. Di, por ejemplo: pon el peso de sede a convenios en 0,8. O pide propuestas al arquitecto causal.");
      setOptions([{ label: "Pedir propuestas", utterance: "Como arquitecto causal, propón como máximo dos cambios de peso o relación justificados, cada uno como opción con su acción peso o relacion." }, { label: "Sin cambios", ceremony: "next" }]);
    } else if (ph.id === "dictamen") {
      direct({ tab: "oracle", focus: { kind: "phase", id: "dictamen" } });
      say(`Propongo este dictamen: ${o.verdict} ¿Lo aceptáis?`);
      const dictate = { label: "Dictar otra decisión", run: () => { c.capture = "decision"; c.onCapture = (txt) => { c.decisions.push(txt); say("Decisión anotada. ¿Alguna más?"); setOptions([dictate, next]); }; say("Dime la decisión."); } };
      setOptions([{ label: "Aceptar dictamen", run: () => { c.decisions.push(o.verdict); say("Dictamen aceptado."); ceremony("next"); } }, dictate, next]);
    } else if (ph.id === "compromisos") {
      direct({ tab: "oracle", focus: { kind: "phase", id: "compromisos" } });
      c.capture = "commitment";
      say("Dime los compromisos: rol, tarea y fecha. Por ejemplo: Fundación, pedir cita con Cultura, 30 de octubre. Cuando acabes, di siguiente.");
      setOptions([{ label: "Terminar compromisos", run: () => { c.capture = null; ceremony("next"); } }]);
    } else if (ph.id === "cierre") {
      c.capture = null;
      direct({ tab: "oracle", focus: { kind: "phase", id: "cierre" } });
      if (!c.decisions.length) c.decisions.push(o.verdict);
      c.after = (ok) => {
        if (ok) { say("Ceremonia registrada. Hasta la próxima."); OD.cer = null; renderPhase(); return; }
        setOptions([{ label: "Registrar la ceremonia", run: () => ceremony("same") }, { label: "Terminar sin registrar", ceremony: "stop" }]);
      };
      propose({ id: "registrar_ceremonia", args: { resumen: ["Ceremonia por voz", ...c.notes].join(" · ").slice(0, 400), decisiones: c.decisions, compromisos: c.commitments } });
      setOptions([]);
    } else { ceremony("next"); }
  }

  // ---- speech input ----
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const IS_SAFARI = /^((?!chrome|android|crios|fxios).)*safari/i.test(navigator.userAgent);
  let rec = null, pressAt = 0, heard = "";
  const MIC_HELP = {
    "not-allowed": IS_SAFARI
      ? "Safari no tiene permiso. Safari › Ajustes para 127.0.0.1 › Micrófono: Permitir. En macOS: Ajustes del Sistema › Privacidad › Micrófono y Reconocimiento de voz para Safari."
      : "El navegador no tiene permiso para el micrófono: pulsa el candado de la barra de direcciones y permítelo.",
    "service-not-allowed": "El reconocimiento de voz del sistema está desactivado. En macOS: Ajustes del Sistema › Teclado › Dictado: activar. Luego recarga la página.",
    "audio-capture": "No encuentro micrófono. Revisa la entrada en Ajustes del Sistema › Sonido.",
    "network": "El servicio de reconocimiento de voz no responde (sin conexión).",
    "language-not-supported": "El reconocimiento en español no está disponible; añade Español en Ajustes del Sistema › Teclado › Dictado › Idiomas.",
  };
  function startListening() {
    unlockTTS();
    if (!SR) { say("Este navegador no tiene reconocimiento de voz. Usa Safari 14.1+ o Chrome, o escribe.", "sys"); return; }
    if (OD.listening) return;
    if ("speechSynthesis" in window) speechSynthesis.cancel();
    heard = "";
    try {
      rec = new SR();
      rec.lang = "es-ES"; rec.interimResults = true; rec.continuous = false; rec.maxAlternatives = 1;
      rec.onresult = (e) => { let txt = ""; for (let k = 0; k < e.results.length; k++) txt += e.results[k][0].transcript; heard = txt; $("od-interim").textContent = txt; };
      rec.onerror = (e) => { if (e.error !== "no-speech" && e.error !== "aborted") say(MIC_HELP[e.error] || ("Micrófono: " + e.error), "sys"); };
      rec.onend = () => {
        OD.listening = false; $("od-mic").classList.remove("on"); $("od-interim").textContent = "";
        const txt = heard.trim(); heard = "";
        if (txt) handle(txt);
      };
      rec.start();
      OD.listening = true; $("od-mic").classList.add("on"); $("od-interim").textContent = "Escuchando…";
    } catch (err) { OD.listening = false; say("No he podido abrir el micrófono: " + err.message, "sys"); }
  }
  function stopListening() { if (rec && OD.listening) { try { rec.stop(); } catch (e) {} } }
  function micDown() { pressAt = Date.now(); if (OD.listening) { stopListening(); pressAt = 0; } else startListening(); }
  function micUp() { if (pressAt && Date.now() - pressAt > 600) stopListening(); pressAt = 0; }
  function toggleMute(force) {
    OD.muted = force ?? !OD.muted; STORE.set("od-muted", OD.muted);
    if (OD.muted && "speechSynthesis" in window) speechSynthesis.cancel();
    $("od-mute").textContent = OD.muted ? "🔇" : "🔊"; $("od-mute").setAttribute("aria-pressed", String(OD.muted));
  }

  async function init(host) {
    H = host;
    let h;
    try { h = await api("/api/health"); } catch (e) { return; }  // static hosting (GitHub Pages): no oracle
    if (!h || !h.local) return;
    buildDock();
    toggleMute(OD.muted);
    const mic = $("od-mic");
    mic.addEventListener("pointerdown", (e) => { e.preventDefault(); micDown(); });
    mic.addEventListener("pointerup", micUp);
    const typing = () => /INPUT|TEXTAREA|SELECT|BUTTON/.test(document.activeElement.tagName);
    document.addEventListener("keydown", (e) => { if (e.code === "Space" && !e.repeat && !typing()) { e.preventDefault(); micDown(); } });
    document.addEventListener("keyup", (e) => { if (e.code === "Space" && !typing()) micUp(); });
    ["pointerdown", "keydown"].forEach((ev) => document.addEventListener(ev, unlockTTS, { capture: true }));
    $("od-form").addEventListener("submit", (e) => { e.preventDefault(); const v = $("od-text").value; $("od-text").value = ""; handle(v); });
    $("od-yes").addEventListener("click", () => confirm(true)); $("od-no").addEventListener("click", () => confirm(false));
    $("od-mute").addEventListener("click", () => toggleMute());
    $("od-toggle").addEventListener("click", () => { const b = $("od-body"); b.hidden = !b.hidden; $("od-toggle").setAttribute("aria-expanded", String(!b.hidden)); });
    if (!SR) say("Tu navegador no reconoce voz: puedes escribir. Safari 14.1+ y Chrome sí lo hacen.", "sys");
    const o = P().oracle || {};
    if (H.page === "espacio") {
      say("Estás en el Espacio. Di «ve a» y el nombre de un nodo, «sigue hacia», «camino de A a B» o «vuelve al diamante».");
      setOptions([{ label: "Ir a la Fundación", utterance: "ve a la fundación" }, { label: "Ir a Política", utterance: "ve a política" },
        { label: "Camino de Bancos a ENISA", utterance: "camino de bancos a enisa" }, { label: "Empezar la ceremonia", ceremony: "start" }]);
    } else {
      say(`Soy el oráculo. ${o.due ? "Toca ceremonia. " : ""}Pregúntame, pídeme que te lleve a algo o di «empieza la ceremonia».`);
      setOptions([{ label: "¿Qué hago ahora?", utterance: "qué hago ahora" }, { label: "Empezar la ceremonia", ceremony: "start" },
        { label: "Lee el dictamen", utterance: "lee el dictamen" }, { label: "Abrir el Espacio 3D", view: { page: "espacio", focus: { kind: "core" } } }]);
    }
  }
  window.OracleDock = { init, say };
})();
