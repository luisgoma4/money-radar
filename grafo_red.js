/* money-radar · Red: the ecosystem graph as a live force-directed network (Obsidian-style).
 * Built on vendor/force-graph.min.js (vasturiano/force-graph, MIT, vendored: no CDN).
 *
 *   const net = GrafoRed.mount(container, { faces, onOpenCall(id) });
 *   net.setData(ecosystem)   // { nodes:[{id,label,kind,status,face,detail,pending}], edges:[{from,to,label,weight,status}] }
 *   net.focus(id)            // select a node, centre and zoom on it
 *   net.clear()
 *
 * Interaction: drag nodes, wheel/pinch zoom, drag the background to pan. Hover lights a node and its
 * neighbours (particles travel cause -> effect); click pins the selection and opens its card; the card
 * lists causes and effects (click to follow). Faces in the legend filter; ⛶ goes full screen.
 */
(function () {
  "use strict";
  const KIND_ES = { entity: "figura legal", face: "cara", product: "producto", partner: "socio", factor: "factor de contexto",
    milestone: "hito", call: "convocatoria", sphere: "esfera del mundo", funder: "financiador" };
  const STATUS_ES = { exists: "existe", planned: "por crear", proposed: "propuesta", external: "socio externo", factor: "factor" };
  const FACE_VAR = { cloudy: "--line-cloudy", semf: "--line-semf", causality: "--line-causality", branchout: "--line-branchout", delfina: "--line-delfina" };

  function el(tag, attrs, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") n.className = v; else if (k.startsWith("on")) n.addEventListener(k.slice(2), v); else n.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) if (kid != null && kid !== false) n.append(kid);
    return n;
  }

  function mount(host, opts) {
    const faces = opts.faces || {};
    const wrap = el("div", { class: "net" });
    const stage = el("div", { class: "net-stage" });
    const search = el("input", { type: "search", class: "net-search", placeholder: "Buscar nodo…", "aria-label": "Buscar un nodo del grafo", list: "net-list" });
    const list = el("datalist", { id: "net-list" });
    const btnFit = el("button", { type: "button", class: "net-btn", title: "Encajar todo", "aria-label": "Encajar todo el grafo" }, "⤧");
    const btnFull = el("button", { type: "button", class: "net-btn", title: "Pantalla completa", "aria-label": "Ver el grafo a pantalla completa", "aria-pressed": "false" }, "⛶");
    const tools = el("div", { class: "net-tools" }, search, list, btnFit, btnFull);
    const legend = el("div", { class: "net-legend", role: "group", "aria-label": "Filtrar por cara" });
    const card = el("aside", { class: "net-card", "aria-live": "polite", hidden: true });
    wrap.append(stage, tools, legend, card);
    host.replaceChildren(wrap);

    // ---- palette from the page's CSS tokens (re-read when the theme changes) ----
    let C = {};
    function palette() {
      const cs = getComputedStyle(document.documentElement), v = (k) => cs.getPropertyValue(k).trim();
      C = { ink: v("--ink"), ink2: v("--ink-2"), muted: v("--muted"), axis: v("--axis"), grid: v("--grid"), surface: v("--surface"),
        page: v("--page"), accent: v("--accent"), sphere: v("--world-sphere"), funder: v("--world-funder"), face: {} };
      for (const [f, k] of Object.entries(FACE_VAR)) C.face[f] = v(k);
    }
    palette();
    // Redraw is continuous (hover light and particles), so a new palette shows on the next frame.
    new MutationObserver(() => { palette(); renderLegend(); }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    if (window.matchMedia) matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { palette(); renderLegend(); });
    const colorOf = (n) => n.kind === "sphere" ? C.sphere : n.kind === "funder" ? C.funder : n.kind === "milestone" || !n.face ? C.muted : C.face[n.face] || C.muted;
    const alpha = (hex, a) => { if (!hex || hex[0] !== "#") return hex; const h = hex.length === 4 ? hex.replace(/#(.)(.)(.)/, "#$1$1$2$2$3$3") : hex;
      return `rgba(${parseInt(h.slice(1, 3), 16)},${parseInt(h.slice(3, 5), 16)},${parseInt(h.slice(5, 7), 16)},${a})`; };

    // ---- state ----
    let data = { nodes: [], links: [] }, byId = {}, nbr = {}, hidden = new Set(), hover = null, sel = null, hl = new Set(), hlLinks = new Set();
    const exists = (n) => !(n.status === "planned" || n.status === "proposed");
    const visible = (n) => !(n.face && hidden.has(n.face));
    function lightUp(n) {
      hl = new Set(); hlLinks = new Set();
      if (n) { hl.add(n.id); (nbr[n.id] || []).forEach(([o, l]) => { hl.add(o); hlLinks.add(l); }); }
    }

    // ---- the force-graph instance ----
    const fg = new ForceGraph(stage)
      .backgroundColor("rgba(0,0,0,0)")
      .nodeId("id")
      .nodeLabel(() => "")  // our own labels and card
      .nodeVisibility(visible)
      .linkVisibility((l) => visible(l.source) && visible(l.target))
      .nodeCanvasObjectMode(() => "replace")
      .nodeCanvasObject((n, ctx, k) => {
        const dim = hl.size && !hl.has(n.id), r = n.r, col = colorOf(n);
        ctx.globalAlpha = dim ? 0.18 : 1;
        ctx.beginPath();
        if (n.kind === "sphere") { ctx.moveTo(n.x, n.y - r * 1.3); ctx.lineTo(n.x + r * 1.1, n.y); ctx.lineTo(n.x, n.y + r * 1.3); ctx.lineTo(n.x - r * 1.1, n.y); ctx.closePath(); }
        else if (n.kind === "funder") ctx.rect(n.x - r * 0.85, n.y - r * 0.85, r * 1.7, r * 1.7);
        else ctx.arc(n.x, n.y, r, 0, 2 * Math.PI);
        if (exists(n) && !n.pending) { ctx.fillStyle = col; ctx.fill(); }
        else { ctx.fillStyle = C.surface; ctx.fill(); ctx.setLineDash(n.pending ? [1.2, 1.6] : [2.2, 1.6]); ctx.lineWidth = 1.4; ctx.strokeStyle = col; ctx.stroke(); ctx.setLineDash([]); }
        if (n.id === sel || n.id === hover) { ctx.lineWidth = 2 / k + 0.6; ctx.strokeStyle = C.accent; ctx.beginPath(); ctx.arc(n.x, n.y, r + 2.5, 0, 2 * Math.PI); ctx.stroke(); }
        // Labels fade in with zoom, like Obsidian; the lit neighbourhood and the big hubs always show theirs.
        const show = hl.size ? hl.has(n.id) : k > 2.1 || (k > 1.1 && n.r >= 7) || n.r >= 10;
        if (show) {
          const fs = Math.max(10 / k, 2.6), txt = n.label.length > 34 && !hl.has(n.id) ? n.label.slice(0, 33) + "…" : n.label;
          ctx.font = `${n.id === sel || n.id === hover ? 600 : 500} ${fs}px -apple-system, system-ui, sans-serif`;
          ctx.textAlign = "center"; ctx.textBaseline = "top";
          ctx.lineWidth = 3 / k; ctx.strokeStyle = alpha(C.page, 0.85); ctx.strokeText(txt, n.x, n.y + r + 2);
          ctx.fillStyle = dim ? C.muted : C.ink; ctx.fillText(txt, n.x, n.y + r + 2);
        }
        ctx.globalAlpha = 1;
      })
      .nodePointerAreaPaint((n, color, ctx) => { ctx.fillStyle = color; ctx.beginPath(); ctx.arc(n.x, n.y, n.r + 2, 0, 2 * Math.PI); ctx.fill(); })
      .linkColor((l) => hlLinks.has(l) ? C.accent : hl.size ? alpha(C.axis, 0.12) : alpha(C.axis, 0.7))
      .linkWidth((l) => (hlLinks.has(l) ? 1.2 : 0.4) + (l.weight ?? 0.5) * (hlLinks.has(l) ? 2.6 : 1.6))
      .linkLineDash((l) => l.status === "planned" ? [3, 2] : null)
      .linkDirectionalArrowLength((l) => hlLinks.has(l) ? 5 : 3)
      .linkDirectionalArrowRelPos(1)
      .linkDirectionalArrowColor((l) => hlLinks.has(l) ? C.accent : hl.size ? alpha(C.axis, 0.12) : C.axis)
      .linkDirectionalParticles((l) => hlLinks.has(l) ? 2 : 0)
      .linkDirectionalParticleWidth(2.2)
      .linkDirectionalParticleSpeed((l) => 0.004 + (l.weight ?? 0.5) * 0.008)
      .linkDirectionalParticleColor(() => C.accent)
      .onNodeHover((n) => { hover = n ? n.id : null; stage.style.cursor = n ? "pointer" : "grab"; if (!sel) lightUp(n); })
      .onNodeClick((n) => select(n.id, false))
      .onBackgroundClick(() => clear())
      .onNodeDragEnd((n) => { n.fx = undefined; n.fy = undefined; })  // released nodes float back, as in Obsidian
      .autoPauseRedraw(false)  // hover light and particles need frames after the layout settles
      .cooldownTicks(260)
      .d3VelocityDecay(0.32);
    fg.d3Force("charge").strength((n) => -40 - n.r * 14).distanceMax(420);
    fg.d3Force("link").distance((l) => 26 + (1 - (l.weight ?? 0.5)) * 46 + (l.source.r || 5) + (l.target.r || 5)).strength((l) => 0.15 + (l.weight ?? 0.5) * 0.5);
    fg.d3Force("collide", collide());
    let fitted = false;
    fg.onEngineStop(() => { if (!fitted) { fitted = true; fg.zoomToFit(500, 40, visible); } });

    function collide() {  // tiny collision force (no d3 import): keeps nodes and their labels from piling up
      let nodes = [];
      const force = (a) => {
        for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
          const p = nodes[i], q = nodes[j], dx = q.x - p.x, dy = q.y - p.y, d = Math.hypot(dx, dy) || 1, min = p.r + q.r + 6;
          if (d < min) { const m = ((min - d) / d) * a * 0.5; p.vx -= dx * m; p.vy -= dy * m; q.vx += dx * m; q.vy += dy * m; }
        }
      };
      force.initialize = (ns) => { nodes = ns; };
      return force;
    }

    // ---- size: follow the container (and full screen) ----
    function resize() {
      const full = document.fullscreenElement === wrap || document.webkitFullscreenElement === wrap;
      fg.width(stage.clientWidth || wrap.clientWidth || 800).height(full ? window.innerHeight : Math.max(380, Math.min(window.innerHeight * 0.72, 720)));
    }
    new ResizeObserver(resize).observe(wrap);
    // Only animate while on screen (the tab may be hidden).
    new IntersectionObserver(([e]) => { e.isIntersecting ? fg.resumeAnimation() : fg.pauseAnimation(); }).observe(wrap);
    function toggleFull() {
      const on = document.fullscreenElement === wrap || document.webkitFullscreenElement === wrap;
      if (on) (document.exitFullscreen || document.webkitExitFullscreen).call(document);
      else (wrap.requestFullscreen || wrap.webkitRequestFullscreen).call(wrap);
    }
    const onFull = () => {
      const on = document.fullscreenElement === wrap || document.webkitFullscreenElement === wrap;
      wrap.classList.toggle("full", on); btnFull.setAttribute("aria-pressed", String(on));
      btnFull.title = on ? "Salir de pantalla completa" : "Pantalla completa"; setTimeout(() => { resize(); fg.zoomToFit(400, 50, visible); }, 120);
    };
    document.addEventListener("fullscreenchange", onFull); document.addEventListener("webkitfullscreenchange", onFull);
    btnFull.addEventListener("click", toggleFull);
    btnFit.addEventListener("click", () => fg.zoomToFit(500, 40, visible));
    search.addEventListener("change", () => {
      const q = search.value.trim().toLowerCase(); if (!q) return;
      const n = data.nodes.find((x) => x.label.toLowerCase() === q) || data.nodes.find((x) => x.label.toLowerCase().includes(q));
      if (n) select(n.id, true); search.value = "";
    });
    wrap.addEventListener("keydown", (e) => { if (e.key === "Escape" && sel) clear(); });

    // ---- selection and card ----
    function select(id, fly) {
      const n = byId[id]; if (!n) return false;
      if (n.face && hidden.has(n.face)) { hidden.delete(n.face); renderLegend(); fg.nodeVisibility(visible); }
      sel = id; lightUp(n); renderCard(n);
      if (fly !== false) { fg.centerAt(n.x, n.y, 600); fg.zoom(Math.max(fg.zoom(), 2.4), 600); }
      return true;
    }
    function clear() { sel = null; lightUp(hover ? byId[hover] : null); card.hidden = true; }
    function renderCard(n) {
      const rel = (dir) => (nbr[n.id] || []).filter(([, l]) => (dir === "in" ? l.to === n.id : l.from === n.id))
        .sort((a, b) => (b[1].weight ?? 0.5) - (a[1].weight ?? 0.5))
        .map(([o, l]) => el("li", {}, el("button", { type: "button", class: "net-link", onclick: () => select(o) }, byId[o].label),
          el("span", { class: "net-w" }, ` ${l.label} · ${(l.weight ?? 0.5).toFixed(2)}${l.status === "planned" ? " · por crear" : ""}`)));
      const causes = rel("in"), effects = rel("out");
      card.replaceChildren(...[
        el("button", { type: "button", class: "net-x", "aria-label": "Cerrar ficha", onclick: clear }, "×"),
        el("div", { class: "net-kind" }, el("span", { class: "net-dot", style: `background:${colorOf(n)}` }),
          `${KIND_ES[n.kind] || n.kind} · ${STATUS_ES[n.status] || n.status}${n.pending ? " · relación por crear" : ""}${n.face && faces[n.face] ? " · " + faces[n.face].name : ""}`),
        el("h3", {}, n.label), n.detail ? el("p", {}, n.detail) : null,
        n.kind === "call" && opts.onOpenCall ? el("button", { type: "button", class: "net-open", onclick: () => opts.onOpenCall(n.id.slice(2)) }, "Ver ficha en el Radar →") : null,
        causes.length ? el("h4", {}, `Causas (${causes.length})`) : null, causes.length ? el("ul", {}, causes) : null,
        effects.length ? el("h4", {}, `Efectos (${effects.length})`) : null, effects.length ? el("ul", {}, effects) : null].filter(Boolean));
      card.hidden = false;
    }

    // ---- legend: faces as filters ----
    function renderLegend() {
      const chips = Object.entries(faces).map(([f, info]) => el("button", { type: "button", class: "net-chip", "aria-pressed": String(!hidden.has(f)),
        title: hidden.has(f) ? "Mostrar " + info.name : "Ocultar " + info.name,
        onclick: () => { hidden.has(f) ? hidden.delete(f) : hidden.add(f); renderLegend(); fg.nodeVisibility(visible).linkVisibility((l) => visible(l.source) && visible(l.target)); } },
        el("span", { class: "net-dot", style: `background:${C.face[f] || C.muted}` }), info.name.split(" ")[0]));
      const world = data.nodes.some((n) => n.kind === "sphere");
      legend.replaceChildren(...[...chips,
        world ? el("span", { class: "net-key" }, el("span", { class: "net-dot dia", style: `background:${C.sphere}` }), "esfera") : null,
        world ? el("span", { class: "net-key" }, el("span", { class: "net-dot sq", style: `background:${C.funder}` }), "financiador") : null,
        el("span", { class: "net-key" }, el("span", { class: "net-dot", style: `background:${C.muted}` }), "hito"),
        el("span", { class: "net-key" }, el("span", { class: "net-dot hol" }), "aún no existe")].filter(Boolean));
    }

    // ---- data ----
    function setData(g) {
      const keep = {};
      data.nodes.forEach((n) => { keep[n.id] = { x: n.x, y: n.y }; });  // switching copies keeps the layout steady
      const nodes = g.nodes.map((n) => Object.assign({}, n, keep[n.id] || {}));
      byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
      const links = g.edges.filter((e) => byId[e.from] && byId[e.to]).map((e) => Object.assign({}, e, { source: e.from, target: e.to }));
      const deg = {};
      links.forEach((l) => { deg[l.from] = (deg[l.from] || 0) + 1; deg[l.to] = (deg[l.to] || 0) + 1; });
      nodes.forEach((n) => { n.r = (n.kind === "sphere" ? 6 : n.kind === "milestone" ? 2 : 2.6) + Math.sqrt(deg[n.id] || 0) * 1.25; });
      nbr = {};
      links.forEach((l) => { (nbr[l.from] = nbr[l.from] || []).push([l.to, l]); (nbr[l.to] = nbr[l.to] || []).push([l.from, l]); });
      data = { nodes, links };
      list.replaceChildren(...nodes.map((n) => el("option", { value: n.label })));
      fitted = Object.keys(keep).length > 0;
      fg.graphData(data);
      if (sel && !byId[sel]) clear(); else if (sel) { lightUp(byId[sel]); renderCard(byId[sel]); }
      renderLegend(); resize();
    }

    return { setData, focus: (id) => select(id, true), clear, fit: () => fg.zoomToFit(500, 40, visible), resize };
  }

  window.GrafoRed = { mount };
})();
