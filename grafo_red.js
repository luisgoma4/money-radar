/* money-radar · Red: the ecosystem graph as a live force-directed network (Obsidian-style), in 2D or 3D.
 * Engines (vendored, MIT, no CDN): vendor/force-graph.min.js (2D canvas) and vendor/3d-force-graph.min.js
 * (WebGL, loaded only the first time 3D is used). Both share one state: selection and card, route, face
 * filters, search, full screen; switching dimension keeps all of it.
 *
 *   const net = GrafoRed.mount(container, { faces, onOpenCall(id), onDim(dim) });
 *   net.setData(ecosystem)   // { nodes:[{id,label,kind,status,face,detail,pending}], edges:[{from,to,label,weight,status}] }
 *   net.dim(2|3)             // switch engine (returns a promise)
 *   net.focus(id)            // select a node, fly to it, open its card
 *   net.path([ids])          // light a route (consecutive nodes), frame it, open the last node's card
 *   net.filter({hide|only|show: [faces]} | "all"), net.fit(), net.fullscreen(on?)  // fullscreen needs a user gesture
 *   net.camera("in"|"out"|"spin"|"stop")
 *   net.state()              // {dim, selected, path, hidden, full}: what the oracle is told the user sees
 *   net.clear()
 *
 * Interaction: drag nodes, wheel/pinch zoom, drag the background to pan (2D) or orbit (3D). Hover lights a node
 * and its neighbours (particles travel cause -> effect); click pins the selection and opens its card; the card
 * lists causes and effects (click to follow). Faces in the legend filter. In 3D the diamond (Fundacion + the
 * three world spheres) is pinned at the centre.
 */
(function () {
  "use strict";
  const KIND_ES = { entity: "figura legal", face: "cara", product: "producto", partner: "socio", factor: "factor de contexto",
    milestone: "hito", call: "convocatoria", sphere: "esfera del mundo", funder: "financiador" };
  const STATUS_ES = { exists: "existe", planned: "por crear", proposed: "propuesta", external: "socio externo", factor: "factor" };
  const FACE_VAR = { cloudy: "--line-cloudy", semf: "--line-semf", causality: "--line-causality", branchout: "--line-branchout", delfina: "--line-delfina" };
  const LIB3D = "vendor/3d-force-graph.min.js";
  const key = (e) => e.from + ">" + e.to;

  function el(tag, attrs, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") n.className = v; else if (k.startsWith("on")) n.addEventListener(k.slice(2), v); else n.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) if (kid != null && kid !== false) n.append(kid);
    return n;
  }
  let lib3d = null;
  function load3d() {
    if (window.ForceGraph3D) return Promise.resolve();
    return lib3d || (lib3d = new Promise((ok, ko) => {
      const s = el("script", { src: LIB3D }); s.onload = ok; s.onerror = () => { lib3d = null; ko(new Error("3D no disponible")); };
      document.head.append(s);
    }));
  }

  function mount(host, opts) {
    const faces = opts.faces || {};
    const wrap = el("div", { class: "net", tabindex: "-1" });
    const stage2 = el("div", { class: "net-stage" });
    const stage3 = el("div", { class: "net-stage net-3d", hidden: true });
    const labels = el("canvas", { class: "net-labels", hidden: true, "aria-hidden": "true" });
    const search = el("input", { type: "search", class: "net-search", placeholder: "Buscar nodo…", "aria-label": "Buscar un nodo del grafo", list: "net-list" });
    const list = el("datalist", { id: "net-list" });
    const btnDim = el("button", { type: "button", class: "net-btn net-dim", title: "Ver en 3D", "aria-label": "Cambiar entre 2D y 3D" }, "3D");
    const btnFit = el("button", { type: "button", class: "net-btn", title: "Encajar todo", "aria-label": "Encajar todo el grafo" }, "⤧");
    const btnFull = el("button", { type: "button", class: "net-btn", title: "Pantalla completa", "aria-label": "Ver el grafo a pantalla completa", "aria-pressed": "false" }, "⛶");
    const tools = el("div", { class: "net-tools" }, search, list, btnDim, btnFit, btnFull);
    const legend = el("div", { class: "net-legend", role: "group", "aria-label": "Filtrar por cara" });
    const card = el("aside", { class: "net-card", "aria-live": "polite", hidden: true });
    const note = el("div", { class: "net-note", role: "status" });
    wrap.append(stage2, stage3, labels, tools, legend, card, note);
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
    const retheme = () => { palette(); renderLegend(); paint3(); };
    new MutationObserver(retheme).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    if (window.matchMedia) matchMedia("(prefers-color-scheme: dark)").addEventListener("change", retheme);
    const colorOf = (n) => n.kind === "sphere" ? C.sphere : n.kind === "funder" ? C.funder : n.kind === "milestone" || !n.face ? C.muted : C.face[n.face] || C.muted;
    const alpha = (hex, a) => { if (!hex || hex[0] !== "#") return hex; const h = hex.length === 4 ? hex.replace(/#(.)(.)(.)/, "#$1$1$2$2$3$3") : hex;
      return `rgba(${parseInt(h.slice(1, 3), 16)},${parseInt(h.slice(3, 5), 16)},${parseInt(h.slice(5, 7), 16)},${a})`; };

    // ---- shared state ----
    let G = { nodes: [], edges: [] }, meta = {}, nbr = {}, hidden = new Set(), hover = null, sel = null, hl = new Set(), hlLinks = new Set(), route = [];
    let dim = 2, fg2 = null, fg3 = null, d2 = { nodes: [], links: [] }, d3 = { nodes: [], links: [] }, by2 = {}, by3 = {};
    const exists = (n) => !(n.status === "planned" || n.status === "proposed");
    const visible = (n) => !(n && n.face && hidden.has(n.face));
    const lvis = (l) => visible(l.source) && visible(l.target);
    const active = () => (dim === 3 ? fg3 : fg2);
    const node = (id) => (dim === 3 ? by3 : by2)[id];
    function lightUp(id) {
      if (route.length) return lightRoute();
      hl = new Set(); hlLinks = new Set();
      if (id) { hl.add(id); (nbr[id] || []).forEach(([o, e]) => { hl.add(o); hlLinks.add(key(e)); }); }
      paint3();
    }
    function lightRoute() {
      hl = new Set(route); hlLinks = new Set();
      for (let i = 0; i + 1 < route.length; i++) {
        const hit = (nbr[route[i]] || []).find(([o]) => o === route[i + 1]); if (hit) hlLinks.add(key(hit[1]));
      }
      paint3();
    }
    const radius = (n) => (n.kind === "sphere" ? 6 : n.kind === "milestone" ? 2 : 2.6) + Math.sqrt(n.deg || 0) * 1.25;

    // ---- 2D engine ----
    fg2 = new ForceGraph(stage2)
      .backgroundColor("rgba(0,0,0,0)")
      .nodeLabel(() => "")  // our own labels and card
      .nodeVisibility(visible).linkVisibility(lvis)
      .nodeCanvasObjectMode(() => "replace")
      .nodeCanvasObject((n, ctx, k) => {
        const dimmed = hl.size && !hl.has(n.id), r = n.r, col = colorOf(n);
        ctx.globalAlpha = dimmed ? 0.18 : 1;
        ctx.beginPath();
        if (n.kind === "sphere") { ctx.moveTo(n.x, n.y - r * 1.3); ctx.lineTo(n.x + r * 1.1, n.y); ctx.lineTo(n.x, n.y + r * 1.3); ctx.lineTo(n.x - r * 1.1, n.y); ctx.closePath(); }
        else if (n.kind === "funder") ctx.rect(n.x - r * 0.85, n.y - r * 0.85, r * 1.7, r * 1.7);
        else ctx.arc(n.x, n.y, r, 0, 2 * Math.PI);
        if (exists(n) && !n.pending) { ctx.fillStyle = col; ctx.fill(); }
        else { ctx.fillStyle = C.surface; ctx.fill(); ctx.setLineDash(n.pending ? [1.2, 1.6] : [2.2, 1.6]); ctx.lineWidth = 1.4; ctx.strokeStyle = col; ctx.stroke(); ctx.setLineDash([]); }
        if (n.id === sel || n.id === hover) { ctx.lineWidth = 2 / k + 0.6; ctx.strokeStyle = C.accent; ctx.beginPath(); ctx.arc(n.x, n.y, r + 2.5, 0, 2 * Math.PI); ctx.stroke(); }
        // Labels fade in with zoom, like Obsidian; with something lit, only the lit ones.
        const show = hl.size ? hl.has(n.id) : k > 2.1 || (k > 1.1 && n.r >= 7) || n.r >= 10;
        if (show) {
          const fs = Math.max(10 / k, 2.6), txt = n.label.length > 34 && !hl.has(n.id) ? n.label.slice(0, 33) + "…" : n.label;
          ctx.font = `${n.id === sel || n.id === hover ? 600 : 500} ${fs}px -apple-system, system-ui, sans-serif`;
          ctx.textAlign = "center"; ctx.textBaseline = "top";
          ctx.lineWidth = 3 / k; ctx.strokeStyle = alpha(C.page, 0.85); ctx.strokeText(txt, n.x, n.y + r + 2);
          ctx.fillStyle = dimmed ? C.muted : C.ink; ctx.fillText(txt, n.x, n.y + r + 2);
        }
        ctx.globalAlpha = 1;
      })
      .nodePointerAreaPaint((n, color, ctx) => { ctx.fillStyle = color; ctx.beginPath(); ctx.arc(n.x, n.y, n.r + 2, 0, 2 * Math.PI); ctx.fill(); })
      .linkColor((l) => hlLinks.has(key(l)) ? C.accent : hl.size ? alpha(C.axis, 0.12) : alpha(C.axis, 0.7))
      .linkWidth((l) => (hlLinks.has(key(l)) ? 1.2 : 0.4) + (l.weight ?? 0.5) * (hlLinks.has(key(l)) ? 2.6 : 1.6))
      .linkLineDash((l) => l.status === "planned" ? [3, 2] : null)
      .linkDirectionalArrowLength((l) => hlLinks.has(key(l)) ? 5 : 3).linkDirectionalArrowRelPos(1)
      .linkDirectionalArrowColor((l) => hlLinks.has(key(l)) ? C.accent : hl.size ? alpha(C.axis, 0.12) : C.axis)
      .linkDirectionalParticles((l) => hlLinks.has(key(l)) ? 2 : 0).linkDirectionalParticleWidth(2.2)
      .linkDirectionalParticleSpeed((l) => 0.004 + (l.weight ?? 0.5) * 0.008).linkDirectionalParticleColor(() => C.accent)
      .onNodeHover((n) => { hover = n ? n.id : null; stage2.style.cursor = n ? "pointer" : "grab"; if (!sel) lightUp(hover); })
      .onNodeClick((n) => select(n.id, false))
      .onBackgroundClick(() => clear())
      .onNodeDragEnd((n) => { n.fx = undefined; n.fy = undefined; })  // released nodes float back, as in Obsidian
      .autoPauseRedraw(false)  // hover light and particles need frames after the layout settles
      .cooldownTicks(260).d3VelocityDecay(0.32);
    forces(fg2);
    let fitted2 = false;
    fg2.onEngineStop(() => { if (!fitted2) { fitted2 = true; fg2.zoomToFit(500, 40, visible); } });

    function forces(fg) {
      fg.d3Force("charge").strength((n) => -40 - n.r * 14).distanceMax(420);
      fg.d3Force("link").distance((l) => 26 + (1 - (l.weight ?? 0.5)) * 46 + (l.source.r || 5) + (l.target.r || 5)).strength((l) => 0.15 + (l.weight ?? 0.5) * 0.5);
      fg.d3Force("collide", collide());
    }
    function collide() {  // tiny collision force (no d3 import): keeps nodes and their labels from piling up
      let nodes = [];
      const force = (a) => {
        for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
          const p = nodes[i], q = nodes[j], dx = q.x - p.x, dy = q.y - p.y, dz = (q.z || 0) - (p.z || 0);
          const d = Math.hypot(dx, dy, dz) || 1, min = p.r + q.r + 6;
          if (d < min) { const m = ((min - d) / d) * a * 0.5; p.vx -= dx * m; p.vy -= dy * m; q.vx += dx * m; q.vy += dy * m;
            if (p.vz !== undefined) { p.vz -= dz * m; q.vz += dz * m; } }
        }
      };
      force.initialize = (ns) => { nodes = ns; };
      return force;
    }

    // ---- 3D engine (created on first use) ----
    let fitted3 = false;
    function make3() {
      fg3 = new ForceGraph3D(stage3, { controlType: "orbit" })
        .backgroundColor("rgba(0,0,0,0)").showNavInfo(false)
        .nodeLabel(() => "").nodeRelSize(1).nodeVal((n) => Math.pow(n.r * (n.kind === "sphere" ? 1.5 : 1), 3))
        .nodeResolution(14).nodeOpacity(1)
        .nodeVisibility(visible).linkVisibility(lvis)
        .linkOpacity(1).linkCurvature(0)
        .linkDirectionalArrowRelPos(1).linkDirectionalParticleWidth(1.6)
        .linkDirectionalParticleSpeed((l) => 0.004 + (l.weight ?? 0.5) * 0.008)
        .onNodeHover((n) => { hover = n ? n.id : null; stage3.style.cursor = n ? "pointer" : "grab"; if (!sel) lightUp(hover); })
        .onNodeClick((n) => select(n.id, false))
        .onBackgroundClick(() => clear())
        .onNodeDragEnd((n) => { if (!n.pin) { n.fx = n.fy = n.fz = undefined; } })
        .cooldownTicks(300).d3VelocityDecay(0.3);
      forces(fg3);
      fg3.onEngineStop(() => { if (!fitted3) { fitted3 = true; fg3.zoomToFit(600, 30, visible); } });
      paint3();
    }
    function paint3() {  // 3D accessors are evaluated once: re-set them so the light, filters and theme apply
      if (!fg3) return;
      const lit = (l) => hlLinks.has(key(l));
      fg3.nodeColor((n) => { const c = colorOf(n), base = exists(n) && !n.pending ? 1 : 0.32; return alpha(c, hl.size && !hl.has(n.id) ? base * 0.15 : base); })
        .linkColor((l) => lit(l) ? C.accent : alpha(C.axis, hl.size ? 0.08 : l.status === "planned" ? 0.35 : 0.7))
        .linkWidth((l) => lit(l) ? 0.6 + (l.weight ?? 0.5) * 1.6 : 0)
        .linkDirectionalArrowLength((l) => lit(l) ? 5 : 2.5)
        .linkDirectionalArrowColor((l) => lit(l) ? C.accent : alpha(C.axis, hl.size ? 0.08 : 0.7))
        .linkDirectionalParticles((l) => lit(l) ? 2 : 0).linkDirectionalParticleColor(() => C.accent)
        .nodeVisibility(visible).linkVisibility(lvis);
    }
    // Labels in 3D: projected each frame onto a 2D overlay (lit nodes, the selection, the hubs).
    function drawLabels() {
      if (dim !== 3 || !fg3) return;
      requestAnimationFrame(drawLabels);
      const w = stage3.clientWidth, h = stage3.clientHeight, dpr = window.devicePixelRatio || 1;
      if (labels.width !== w * dpr || labels.height !== h * dpr) { labels.width = w * dpr; labels.height = h * dpr; labels.style.width = w + "px"; labels.style.height = h + "px"; }
      const ctx = labels.getContext("2d"); ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, w, h);
      ctx.textAlign = "center"; ctx.textBaseline = "top";
      for (const n of d3.nodes) {
        if (!visible(n) || n.x === undefined) continue;
        const show = hl.size ? hl.has(n.id) : n.r >= 9 || n.kind === "sphere";
        if (!show) continue;
        const p = fg3.graph2ScreenCoords(n.x, n.y, n.z);
        if (p.x < -50 || p.y < -20 || p.x > w + 50 || p.y > h + 20) continue;
        const txt = n.label.length > 34 && !hl.has(n.id) ? n.label.slice(0, 33) + "…" : n.label;
        ctx.font = `${n.id === sel || n.id === hover ? 600 : 500} 11.5px -apple-system, system-ui, sans-serif`;
        ctx.lineWidth = 3; ctx.strokeStyle = alpha(C.page, 0.85); ctx.strokeText(txt, p.x, p.y + 9);
        ctx.fillStyle = C.ink; ctx.fillText(txt, p.x, p.y + 9);
      }
    }
    function pinDiamond(nodes) {  // the tetrahedron at the centre: Fundacion on top, the three spheres as the base
      const R = 70, spheres = ["s_politica", "s_bancos", "s_arte"].filter((i) => nodes.some((n) => n.id === i));
      nodes.forEach((n) => { n.pin = false; });
      if (!spheres.length) return;
      const at = { e_fund: [0, R, 0] };
      spheres.forEach((id, i) => { const a = (i / 3) * Math.PI * 2, r = R * Math.sqrt(8) / 3; at[id] = [r * Math.cos(a), -R / 3, r * Math.sin(a)]; });
      nodes.forEach((n) => { if (at[n.id]) { [n.fx, n.fy, n.fz] = at[n.id]; n.pin = true; } else { n.fx = n.fy = n.fz = undefined; } });
    }

    // ---- switching dimension ----
    async function setDim(d) {
      d = d === 3 ? 3 : 2;
      if (d === 3 && !fg3) {
        note.textContent = "Cargando el 3D…";
        try { await load3d(); } catch (e) { note.textContent = "No se pudo cargar el 3D."; return false; }
        note.textContent = ""; make3(); build3();
      }
      dim = d;
      stage2.hidden = dim !== 2; stage3.hidden = labels.hidden = dim !== 3;
      btnDim.textContent = dim === 3 ? "2D" : "3D"; btnDim.title = dim === 3 ? "Ver en 2D" : "Ver en 3D";
      wrap.classList.toggle("is3d", dim === 3);
      if (dim === 3) { fg2.pauseAnimation(); fg3.resumeAnimation(); paint3(); requestAnimationFrame(drawLabels); }
      else { if (fg3) fg3.pauseAnimation(); fg2.resumeAnimation(); }
      resize();
      if (route.length) frame(route); else if (sel) fly(sel); else active().zoomToFit(500, 40, visible);
      if (opts.onDim) opts.onDim(dim);
      return true;
    }
    btnDim.addEventListener("click", () => setDim(dim === 3 ? 2 : 3));

    // ---- size, visibility on screen, full screen ----
    function height() { return isFull() ? window.innerHeight : Math.max(380, Math.min(window.innerHeight * 0.72, 720)); }
    function resize() {
      const w = wrap.clientWidth || 800, h = height();
      fg2.width(w).height(h); if (fg3) fg3.width(w).height(h);
    }
    new ResizeObserver(resize).observe(wrap);
    new IntersectionObserver(([e]) => {  // only animate while on screen (the tab may be hidden)
      const a = active(); if (!a) return; e.isIntersecting ? a.resumeAnimation() : a.pauseAnimation();
    }).observe(wrap);
    function isFull() { return document.fullscreenElement === wrap || document.webkitFullscreenElement === wrap; }
    function toggleFull() {
      // Browsers only allow full screen from a click: a voice request may be refused (the oracle then offers a button).
      const r = isFull() ? (document.exitFullscreen || document.webkitExitFullscreen).call(document) : (wrap.requestFullscreen || wrap.webkitRequestFullscreen).call(wrap);
      if (r && r.catch) r.catch(() => {});
    }
    function fullscreen(on) { if (on === undefined || on !== isFull()) try { toggleFull(); } catch (e) { return false; } return true; }
    const onFull = () => {
      const on = isFull();
      wrap.classList.toggle("full", on); btnFull.setAttribute("aria-pressed", String(on));
      btnFull.title = on ? "Salir de pantalla completa" : "Pantalla completa"; setTimeout(() => { resize(); active().zoomToFit(400, 50, visible); }, 120);
    };
    document.addEventListener("fullscreenchange", onFull); document.addEventListener("webkitfullscreenchange", onFull);
    btnFull.addEventListener("click", toggleFull);
    btnFit.addEventListener("click", () => fit());
    search.addEventListener("change", () => {
      const q = search.value.trim().toLowerCase(); if (!q) return;
      const n = G.nodes.find((x) => x.label.toLowerCase() === q) || G.nodes.find((x) => x.label.toLowerCase().includes(q));
      if (n) select(n.id, true); search.value = "";
    });
    wrap.addEventListener("keydown", (e) => { if (e.key === "Escape" && sel) clear(); });

    // ---- camera ----
    function fly(id) {
      const n = node(id); if (!n || n.x === undefined) return;
      if (dim === 2) { fg2.centerAt(n.x, n.y, 600); fg2.zoom(Math.max(fg2.zoom(), 2.4), 600); return; }
      const d = 110, r = 1 + d / (Math.hypot(n.x, n.y, n.z) || 1);
      fg3.cameraPosition({ x: n.x * r, y: n.y * r, z: n.z * r }, { x: n.x, y: n.y, z: n.z }, 900);
    }
    function frame(ids) { const set = new Set(ids); active().zoomToFit(700, 80, (n) => set.has(n.id)); }
    function fit() { if (!route.length) clear(); active().zoomToFit(500, 40, visible); }
    function camera(c) {
      if (dim === 2) {
        if (c === "in" || c === "out") fg2.zoom(fg2.zoom() * (c === "in" ? 1.6 : 1 / 1.6), 500);
        return;
      }
      const ctl = fg3.controls();
      if (c === "spin" || c === "stop") { ctl.autoRotate = c === "spin"; ctl.autoRotateSpeed = 1.2; return; }
      const p = fg3.cameraPosition(), k = c === "in" ? 0.6 : 1.6, t = ctl.target || { x: 0, y: 0, z: 0 };
      fg3.cameraPosition({ x: t.x + (p.x - t.x) * k, y: t.y + (p.y - t.y) * k, z: t.z + (p.z - t.z) * k }, null, 600);
    }

    // ---- routes, filters ----
    function path(ids) {
      ids = (ids || []).filter((i) => meta[i]); if (!ids.length) return false;
      ids.forEach((i) => { const f = meta[i].face; if (f) hidden.delete(f); });
      renderLegend(); applyVisibility();
      route = ids; sel = ids[ids.length - 1]; lightRoute(); renderCard(meta[sel]);
      frame(ids);
      return true;
    }
    function applyVisibility() { fg2.nodeVisibility(visible).linkVisibility(lvis); paint3(); }
    function filter(f) {
      const all = Object.keys(faces);
      if (f === "all" || !f) hidden = new Set();
      else if (f.only) hidden = new Set(all.filter((x) => !f.only.includes(x)));
      else if (f.hide) f.hide.forEach((x) => hidden.add(x));
      if (f && f.show) f.show.forEach((x) => hidden.delete(x));
      if (sel && meta[sel] && !visible(meta[sel])) clear();
      renderLegend(); applyVisibility(); setTimeout(() => active().zoomToFit(500, 40, visible), 50);
    }

    // ---- selection and card ----
    function select(id, flyTo) {
      const n = meta[id]; if (!n) return false;
      if (n.face && hidden.has(n.face)) { hidden.delete(n.face); renderLegend(); applyVisibility(); }
      route = []; sel = id; lightUp(id); renderCard(n);
      if (flyTo !== false) fly(id);
      return true;
    }
    function clear() { sel = null; route = []; lightUp(hover); card.hidden = true; }
    function renderCard(n) {
      const rel = (dir) => (nbr[n.id] || []).filter(([, e]) => (dir === "in" ? e.to === n.id : e.from === n.id))
        .sort((a, b) => (b[1].weight ?? 0.5) - (a[1].weight ?? 0.5))
        .map(([o, e]) => el("li", {}, el("button", { type: "button", class: "net-link", onclick: () => select(o) }, meta[o].label),
          el("span", { class: "net-w" }, ` ${e.label} · ${(e.weight ?? 0.5).toFixed(2)}${e.status === "planned" ? " · por crear" : ""}`)));
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
        onclick: () => { hidden.has(f) ? hidden.delete(f) : hidden.add(f); renderLegend(); applyVisibility(); } },
        el("span", { class: "net-dot", style: `background:${C.face[f] || C.muted}` }), info.name.split(" ")[0]));
      const world = G.nodes.some((n) => n.kind === "sphere");
      legend.replaceChildren(...[...chips,
        world ? el("span", { class: "net-key" }, el("span", { class: "net-dot dia", style: `background:${C.sphere}` }), "esfera") : null,
        world ? el("span", { class: "net-key" }, el("span", { class: "net-dot sq", style: `background:${C.funder}` }), "financiador") : null,
        el("span", { class: "net-key" }, el("span", { class: "net-dot", style: `background:${C.muted}` }), "hito"),
        el("span", { class: "net-key" }, el("span", { class: "net-dot hol" }), "aún no existe")].filter(Boolean));
    }

    // ---- data: one graph, one copy per engine (the simulations own their coordinates) ----
    function copy(prev) {
      const keep = {};
      prev.nodes.forEach((n) => { keep[n.id] = { x: n.x, y: n.y, z: n.z }; });  // switching copies keeps the layout steady
      const nodes = G.nodes.map((n) => Object.assign({}, n, keep[n.id] || {}));
      const links = G.edges.map((e) => Object.assign({}, e, { source: e.from, target: e.to }));
      return { data: { nodes, links }, by: Object.fromEntries(nodes.map((n) => [n.id, n])), kept: Object.keys(keep).length > 0 };
    }
    function build3() {
      if (!fg3) return;
      const c = copy(d3); d3 = c.data; by3 = c.by; fitted3 = c.kept;
      pinDiamond(d3.nodes);
      fg3.graphData(d3); paint3();
    }
    function setData(g) {
      meta = Object.fromEntries(g.nodes.map((n) => [n.id, Object.assign({}, n)]));
      const edges = g.edges.filter((e) => meta[e.from] && meta[e.to]);
      const deg = {};
      edges.forEach((e) => { deg[e.from] = (deg[e.from] || 0) + 1; deg[e.to] = (deg[e.to] || 0) + 1; });
      Object.values(meta).forEach((n) => { n.deg = deg[n.id] || 0; n.r = radius(n); });
      G = { nodes: Object.values(meta), edges };
      nbr = {};
      edges.forEach((e) => { (nbr[e.from] = nbr[e.from] || []).push([e.to, e]); (nbr[e.to] = nbr[e.to] || []).push([e.from, e]); });
      list.replaceChildren(...G.nodes.map((n) => el("option", { value: n.label })));
      const c = copy(d2); d2 = c.data; by2 = c.by; fitted2 = c.kept;
      fg2.graphData(d2);
      build3();
      route = route.filter((i) => meta[i]);
      if (sel && !meta[sel]) clear(); else if (route.length) { lightRoute(); renderCard(meta[sel]); } else if (sel) { lightUp(sel); renderCard(meta[sel]); }
      renderLegend(); resize();
    }

    return { setData, dim: setDim, focus: (id) => select(id, true), path, filter, fullscreen, camera, clear, fit, resize,
      state: () => ({ dim, selected: sel, path: route.slice(), hidden: [...hidden], full: isFull() }) };
  }

  window.GrafoRed = { mount };
})();
