"""money-radar: strategy planner — one project, many faces.

Combines the opportunities in money.db with strategy.json (faces = projects,
entities = legal figures, milestones = prerequisites, per-call strategy) and
computes, for every call:

- grade A–D from fit, value, readiness, timing and strategic weight (0–100)
- the applicant entity to use and whether it exists yet
- the value level offered (1 single face, 2 lead + support, 3 whole project)
- a backward calendar from the deadline (or the estimated next edition)
- which pending milestones block it

and ranks the milestones by how much value they unlock.

Used by build.py (dashboard) and orchestrator.py (guide). Standard library only;
loaded by file path because ``python3 -I`` drops the script dir from sys.path.
Value scores are planning judgements documented in strategy.json, not amounts.
"""

import datetime as dt
import json
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
STRATEGY = HERE / "strategy.json"

WEIGHTS = {"fit": 25, "value": 25, "readiness": 25, "timing": 15, "strategic": 10}
GRADES = [(70, "A"), (55, "B"), (40, "C"), (0, "D")]
ENTITY_READY = {"exists", "external"}


def load_strategy(path=STRATEGY):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _date(s):
    return dt.date.fromisoformat(s) if s else None


def target_deadline(o, today):
    """(date, estimated?) to plan against: the real deadline, or next edition ≈ +1 year."""
    d = _date(o.get("deadline"))
    if d and d >= today:
        return d, False
    if d:  # closed: assume a yearly call one year later
        nxt = d
        while nxt < today:
            nxt = nxt.replace(year=nxt.year + 1)
        return nxt, True
    return None, False


def default_strategy(o):
    lines = o["lines"].split(",")
    company = o["kind"] in ("loan", "equity", "prize") or lines == ["branchout"]
    return {"applicant": ["branchout_sl"] if company else ["fundacion_dl"], "lead": lines[0],
            "support": lines[1:], "level": 1 if len(lines) == 1 else 2, "fit": 2, "value": 2,
            "strategic": 0, "prep_weeks": 6, "prereqs": [], "offer": "", "ask": "",
            "value_basis": "Sin estrategia específica en strategy.json (valores por defecto).", "default": True}


def plan_one(o, st, strategy, today):
    ms = strategy["milestones"]
    entities = strategy["entities"]
    prereqs = list(dict.fromkeys(st.get("prereqs", [])))
    # entity requirements count as prerequisites for the chosen applicant
    applicant = next((e for e in st["applicant"] if entities.get(e, {}).get("status") in ENTITY_READY),
                     st["applicant"][0])
    for m in entities.get(applicant, {}).get("requires", []):
        if m not in prereqs:
            prereqs.append(m)
    pending = [m for m in prereqs if ms.get(m, {}).get("status") != "done"]
    readiness = 1.0 if not prereqs else 1 - len(pending) / len(prereqs)

    target, estimated = target_deadline(o, today)
    prep_days = st.get("prep_weeks", 6) * 7
    block_days = max([ms[m]["weeks"] * 7 for m in pending if m in ms] or [0])
    if o["status"] == "rolling":
        timing, start_by = 0.8, None
    elif target is None:
        timing, start_by = 0.5, None
    else:
        start_by = target - dt.timedelta(days=prep_days)
        slack = (start_by - today).days - block_days
        timing = 1.0 if slack >= 0 else (0.4 if (target - today).days > 0 else 0)
        if estimated:
            timing *= 0.7

    parts = {
        "fit": WEIGHTS["fit"] * st.get("fit", 2) / 5,
        "value": WEIGHTS["value"] * st.get("value", 2) / 5,
        "readiness": WEIGHTS["readiness"] * readiness,
        "timing": WEIGHTS["timing"] * timing,
        "strategic": WEIGHTS["strategic"] * st.get("strategic", 0) / 2,
    }
    score = round(sum(parts.values()))
    grade = next(g for t, g in GRADES if score >= t)
    faces = [st["lead"]] + [f for f in st.get("support", []) if f != st["lead"]]
    return {
        "id": o["id"], "name": o["name"], "grade": grade, "score": score,
        "parts": {k: round(v, 1) for k, v in parts.items()},
        "applicant": applicant, "applicant_status": entities.get(applicant, {}).get("status", "unknown"),
        "level": st.get("level", 1), "lead": st["lead"], "faces": faces,
        "offer": st.get("offer", ""), "ask": st.get("ask", ""), "value_basis": st.get("value_basis", ""),
        "typical_award_eur": st.get("typical_award_eur"),
        "prereqs": prereqs, "pending": pending, "readiness": round(readiness, 2),
        "target": target.isoformat() if target else None, "target_estimated": estimated,
        "start_by": start_by.isoformat() if start_by else None,
        "milestones_by": (start_by - dt.timedelta(days=block_days)).isoformat() if start_by and pending else None,
        "archived": bool(o.get("archived")), "status": o["status"],
        "default": bool(st.get("default")),
    }


def make_plan(opps, strategy, applications=(), today=None, ceremonies=()):
    today = today or dt.date.today()
    plans = {}
    for o in opps:
        st = strategy["opportunities"].get(o["id"]) or default_strategy(o)
        plans[o["id"]] = plan_one(o, st, strategy, today)

    # Milestones ranked by the value they unlock (sum of value points of blocked calls).
    ms = []
    for mid, m in strategy["milestones"].items():
        blocked = [p for p in plans.values() if mid in p["pending"] and not (p["archived"] and not p["target_estimated"])]
        unlock = sum(strategy["opportunities"].get(p["id"], {}).get("value", 2) for p in blocked)
        ms.append({"id": mid, **m, "unlocks": unlock, "blocked": [p["id"] for p in blocked],
                   "grades": sorted(p["grade"] for p in blocked)})
    ms.sort(key=lambda m: (m["status"] == "done", -m["unlocks"], m["weeks"]))

    order = {"A": 0, "B": 1, "C": 2, "D": 3}
    ranking = sorted(plans.values(), key=lambda p: (p["archived"] and not p["target_estimated"],
                                                     order[p["grade"]], -p["score"]))
    now = what_now(plans, ms, [p["id"] for p in ranking], strategy["stages"], applications, today)
    events = calendar(opps, plans, ms, today)
    gantt_rows = gantt(opps, plans, ms, today)
    eco = ecosystem(strategy, plans, [p["id"] for p in ranking], ms, opps)
    causal = causal_summary(eco)
    # The same graph without the diamond and the funders: the Grafo tab and the Espacio switch between both.
    eco_base = ecosystem(without_world(strategy), plans, [p["id"] for p in ranking], ms, opps)
    plan = {
        "today": today.isoformat(), "now": now, "calendar": events, "gantt": gantt_rows,
        "ecosystem": eco, "causal": causal, "ecosystem_base": eco_base, "causal_base": causal_summary(eco_base),
        "project": strategy["project"], "faces": strategy["faces"], "entities": strategy["entities"],
        "levels": strategy["levels"], "stages": strategy["stages"],
        "municipalities": strategy["municipalities"],
        "milestones": ms, "plans": plans, "ranking": [p["id"] for p in ranking],
        "applications": list(applications),
    }
    plan["oracle"] = oracle(plan, eco, causal, strategy, ceremonies, today)
    return plan


def what_now(plans, milestones, ranking, stages, applications, today, n_milestones=5, n_recommended=8):
    """The orchestrator's 'estado' view: active applications, milestones to move, calls to start."""
    ids = {a["opportunity_id"] for a in applications}
    stage_ix = {st["id"]: i for i, st in enumerate(stages)}
    active = []
    for a in applications:
        if a["stage"] == "cerrar":
            continue
        p = plans.get(a["opportunity_id"], {})
        i = stage_ix.get(a["stage"], 0)
        active.append({"id": a["opportunity_id"], "name": p.get("name", a["opportunity_id"]), "stage": a["stage"],
                       "stage_n": i + 1, "stages": len(stages), "stage_name": stages[i]["name"],
                       "checklist": stages[i]["checklist"], "target": p.get("target"), "start_by": p.get("start_by"),
                       "late": bool(p.get("start_by") and p["start_by"] < today.isoformat() and a["stage"] in
                                    ("detectar", "cualificar", "estrategia", "preparar")),
                       "updated_on": a["updated_on"]})
    active.sort(key=lambda x: x["start_by"] or "9999")
    recommended = []
    for oid in ranking:
        p = plans[oid]
        if oid in ids or (p["archived"] and not p["target_estimated"]) or p["grade"] == "D":
            continue
        if p["start_by"]:
            when = "late" if p["start_by"] < today.isoformat() else "start_by"
        else:
            when = "rolling" if p["status"] == "rolling" else "tbd"
        recommended.append({"id": oid, "name": p["name"], "grade": p["grade"], "score": p["score"], "when": when,
                            "start_by": p["start_by"], "target": p["target"], "pending": p["pending"]})
        if len(recommended) == n_recommended:
            break
    todo = [m for m in milestones if m["status"] != "done"][:n_milestones]
    return {"active": active, "milestones": [m["id"] for m in todo], "recommended": recommended}


def calendar(opps, plans, milestones, today, horizon_days=400):
    """Dated events for the agenda. 'late' events are past start dates still worth acting on."""
    end = today + dt.timedelta(days=horizon_days)
    ev = []

    def add(date, kind, ref, name, lines, detail="", estimated=False):
        d = _date(date)
        if d and (d >= today or kind == "late") and d <= end:
            ev.append({"date": d.isoformat(), "kind": kind, "ref": ref, "name": name, "lines": lines,
                       "detail": detail, "estimated": estimated})

    for o in opps:
        p = plans[o["id"]]
        lines = o["lines"].split(",")
        live = not o.get("archived") or p["target_estimated"]
        if not live:
            if o.get("review_on"):
                add(o["review_on"], "review", o["id"], o["name"], lines, o.get("archive_reason") or "")
            continue
        if p["target"]:
            add(p["target"], "next_edition" if p["target_estimated"] else "deadline", o["id"], o["name"], lines,
                o.get("amount_text") or "", p["target_estimated"])
        if o.get("opens") and not o.get("archived"):
            add(o["opens"], "opens", o["id"], o["name"], lines)
        if p["start_by"]:
            late = p["start_by"] < today.isoformat() and p["target"] and p["target"] >= today.isoformat()
            add(p["start_by"], "late" if late else "start_by", o["id"], o["name"], lines,
                f"Grado {p['grade']} · prepara ~{(_date(p['target']) - _date(p['start_by'])).days // 7} semanas",
                p["target_estimated"])
        if o.get("archived") and o.get("review_on"):
            add(o["review_on"], "review", o["id"], o["name"], lines, o.get("archive_reason") or "")
    for m in milestones:
        if m["status"] == "done":
            continue
        need = [plans[i]["milestones_by"] for i in m["blocked"] if plans[i]["milestones_by"]]
        future = [d for d in need if d >= today.isoformat()]
        if future:
            add(min(future), "milestone", m["id"], m["name"], [], f"Necesario para {len(m['blocked'])} convocatorias · ~{m['weeks']} semanas")
        elif need:
            add(min(need), "late", m["id"], m["name"], [], f"Hito atrasado · ~{m['weeks']} semanas")
    ev.sort(key=lambda e: (e["date"], e["kind"]))
    return ev


ASCII_MAP = {"·": "-", "→": "->", "←": "<-", "≈": "~", "«": '"', "»": '"', "“": '"', "”": '"',
             "‘": "'", "’": "'", "–": "-", "—": "-", "…": "...", "€": "EUR", "º": "o", "ª": "a", "+": "+"}


def ascii_text(text):
    """Plain ASCII for the 3D viewer: no accents, no typographic symbols (they break there)."""
    for k, v in ASCII_MAP.items():
        text = text.replace(k, v)
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if ord(c) < 128 and not unicodedata.combining(c))


# Legal entities / faces of strategy.json mapped onto graph nodes.
ENTITY_NODE = {"fundacion_dl": "e_fund", "asoc_local": "e_asoc", "semf_asoc": "e_semf", "causality_ent": "e_cg",
               "branchout_sl": "e_bo", "partner_csic": "x_csic", "partner_uned": "x_uned", "partner_research": "x_idiphisa"}
FACE_NODE = {"cloudy": "f_cloudy", "semf": "f_semf", "causality": "f_cg", "branchout": "f_bo", "delfina": "e_fund"}
NODE_STATUSES = ("exists", "planned", "proposed", "external", "factor")
WORLD_KINDS = ("sphere", "funder")          # the outside world: no face of ours
OUR_KINDS = ("entity", "face", "product", "factor", "milestone", "partner")


def funder_edges(strategy):
    """World surface: sphere -> funder (channels) and funder -> call (convenes)."""
    nodes, edges = [], []
    sphere_label = {n["id"]: n["label"] for n in strategy.get("graph", {}).get("nodes", []) if n.get("type") == "sphere"}
    for f in strategy.get("funders", []):
        nid = f.get("node") or f["id"]
        if not f.get("node"):
            spheres = ", ".join(sphere_label.get(k, k) for k in f.get("spheres", {}))
            nc = len(f.get("calls", []))
            nodes.append({"id": nid, "label": f["label"], "type": "funder", "kind": "funder", "status": "external",
                          "face": None, "detail": f"Financiador · esfera: {spheres} · convoca {nc} "
                                                  + ("convocatoria" if nc == 1 else "convocatorias") + " del radar"})
        for sph, w in f.get("spheres", {}).items():
            edges.append({"from": sph, "to": nid, "label": "canaliza", "status": "exists", "weight": w})
        for c in f.get("calls", []):
            edges.append({"from": nid, "to": "c_" + c, "label": "convoca", "status": "exists", "weight": 1.0})
    return nodes, edges


def without_world(strategy):
    """The strategy as it was before the diamond: no world spheres, no funders (and no edges touching them)."""
    g = strategy.get("graph", {})
    world = {n["id"] for n in g.get("nodes", []) if n.get("type") in WORLD_KINDS}
    return dict(strategy, funders=[], graph=dict(
        g, columns=[c for c in g.get("columns", []) if c["id"] not in WORLD_KINDS],
        nodes=[n for n in g.get("nodes", []) if n["id"] not in world],
        edges=[e for e in g.get("edges", []) if e["from"] not in world and e["to"] not in world]))


def _call_edges(oid, st, applicant, pending_ms=None, applicant_status="exists"):
    """Relations of one call (c_<id>): applicant, faces, products, milestones. Direction cause -> effect."""
    cid = "c_" + oid
    en = ENTITY_NODE.get(applicant)
    out = []
    if en:
        out.append({"from": en, "to": cid, "label": "solicita", "weight": 1.0,
                    "status": "exists" if applicant_status in ("exists", "external") else "planned"})
    faces = [st["lead"]] + [f for f in st.get("support", []) if f != st["lead"]]
    for i, f in enumerate(faces):
        fn = FACE_NODE.get(f)
        if fn and fn != en:
            w = round(0.3 + 0.1 * st.get("fit", 2), 2) if i == 0 else 0.3
            out.append({"from": fn, "to": cid, "label": "lidera la oferta" if i == 0 else "apoya la oferta",
                        "weight": w, "status": "planned"})
    for pr in st.get("products", []):
        out.append({"from": pr, "to": cid, "label": "evidencia", "weight": 0.5, "status": "planned"})
    for m in st.get("prereqs_all", st.get("prereqs", [])):
        out.append({"from": "m_" + m, "to": cid, "label": "habilita", "weight": 1.0,
                    "status": "planned" if pending_ms is None or m in pending_ms else "exists"})
    return out


def ecosystem(strategy, plans, ranking, milestones, opps):
    """The single ecosystem graph used by BOTH the 2D diagram and the 3D viewer."""
    g = strategy.get("graph", {})
    names = {o["id"]: o for o in opps}
    nodes = [dict(n, kind=n["type"]) for n in g.get("nodes", [])]
    edges = [dict(e) for e in g.get("edges", [])]
    fn, fe = funder_edges(strategy)
    nodes += fn
    edges += fe
    used_ms = set()
    for oid in ranking:
        p = plans[oid]
        if p["archived"] and not p["target_estimated"]:
            continue
        st = dict(strategy["opportunities"].get(oid) or default_strategy(names[oid]))
        st["prereqs_all"] = p["prereqs"]
        used_ms.update(p["prereqs"])
        o = names[oid]
        nodes.append({"id": "c_" + oid, "label": p["name"], "type": "call", "kind": "call",
                      "status": "planned" if p["target_estimated"] else "exists", "face": p["lead"],
                      "grade": p["grade"], "score": p["score"],
                      "detail": (f"Grado {p['grade']} ({p['score']}) · " + (o.get("amount_text") or "")
                                 + (f" · plazo {p['target']}" if p["target"] else "")
                                 + (" (próxima edición estimada)" if p["target_estimated"] else ""))})
        edges += _call_edges(oid, st, p["applicant"], set(p["pending"]), p["applicant_status"])
    for m in milestones:
        if m["id"] in used_ms:
            nodes.append({"id": "m_" + m["id"], "label": m["name"], "type": "milestone", "kind": "milestone",
                          "status": "exists" if m["status"] == "done" else "planned", "face": None,
                          "detail": f"{m['detail']} (~{m['weeks']} semanas)"})
    ids = {n["id"] for n in nodes}
    edges = [e for e in edges if e["from"] in ids and e["to"] in ids]
    # A partner is "pending" (shaded) until it has a real relationship with OUR side of the graph.
    kind = {n["id"]: n["kind"] for n in nodes}
    linked = {x for e in edges if e["status"] == "exists" and kind[e["from"]] in OUR_KINDS and kind[e["to"]] in OUR_KINDS
              for x in (e["from"], e["to"])}
    for n in nodes:
        n["pending"] = n["kind"] == "partner" and n["id"] not in linked
    return {"columns": g.get("columns", []), "nodes": nodes, "edges": edges}


# ---------------------------------------------------------------- causal analysis
def _links(eco):
    ch, pa = {}, {}
    for e in eco["edges"]:
        ch.setdefault(e["from"], []).append((e["to"], e.get("weight", 0.5)))
        pa.setdefault(e["to"], []).append((e["from"], e.get("weight", 0.5)))
    return ch, pa


def _reach(adj, start):
    seen, todo = set(), [start]
    while todo:
        for nxt, _ in adj.get(todo.pop(), []):
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    return seen


def topo_order(eco):
    """Kahn's algorithm; returns (order, cycle_nodes)."""
    ids = [n["id"] for n in eco["nodes"]]
    ch, pa = _links(eco)
    indeg = {i: len(pa.get(i, [])) for i in ids}
    order, todo = [], [i for i in ids if indeg[i] == 0]
    while todo:
        n = todo.pop()
        order.append(n)
        for c, _ in ch.get(n, []):
            indeg[c] -= 1
            if indeg[c] == 0:
                todo.append(c)
    return order, [i for i in ids if indeg[i] > 0]


def paths(eco, a, b, limit=500):
    """Directed paths a -> b with their weight (product of edge weights: Wright's path rule)."""
    ch, _ = _links(eco)
    out = []

    def walk(n, path, w):
        if len(out) >= limit:
            return
        if n == b:
            out.append((path, round(w, 4)))
            return
        for c, ew in ch.get(n, []):
            if c not in path:
                walk(c, path + [c], w * ew)
    walk(a, [a], 1.0)
    return sorted(out, key=lambda x: -x[1])


def analyze(eco, t, y):
    """Total/direct effect, mediators, confounders and a back-door adjustment set for t -> y."""
    ch, pa = _links(eco)
    ps = paths(eco, t, y)
    direct = next((w for c, w in ch.get(t, []) if c == y), 0.0)
    med = {}
    for path, w in ps:
        for n in path[1:-1]:
            med[n] = med.get(n, 0) + w
    desc_t = _reach(ch, t)
    anc_t = _reach(pa, t)
    no_t = {k: [(c, w) for c, w in v if c != t] for k, v in ch.items() if k != t}  # graph with T removed

    def reaches_y_avoiding_t(z):
        return y in _reach(no_t, z)
    # Confounder: common cause of T and Y with an open path to Y that does not go through T.
    confounders = sorted(z for z in anc_t if z not in desc_t and reaches_y_avoiding_t(z))
    # Parent adjustment (Pearl): conditioning on T's parents blocks every back-door path.
    adjust = sorted(p for p, _ in pa.get(t, []) if p not in desc_t and reaches_y_avoiding_t(p))
    return {"treatment": t, "outcome": y, "paths": ps[:20], "n_paths": len(ps),
            "total": round(sum(w for _, w in ps), 4), "direct": direct, "indirect": round(sum(w for _, w in ps) - direct, 4),
            "mediators": sorted(med.items(), key=lambda kv: -kv[1]), "confounders": confounders, "adjust": adjust}


def flows(eco, targets):
    """Weighted path flow through each node into the target set (forward x backward DP on the DAG)."""
    order, cyc = topo_order(eco)
    if cyc:
        return {}
    ch, pa = _links(eco)
    fin = {n: (1.0 if not pa.get(n) else 0.0) for n in order}
    for n in order:
        for c, w in ch.get(n, []):
            fin[c] += fin[n] * w
    fout = {n: (1.0 if n in targets else 0.0) for n in order}
    for n in reversed(order):
        if n not in targets:
            fout[n] = sum(fout[c] * w for c, w in ch.get(n, []))
    return {n: fin[n] * fout[n] for n in order}


def causal_summary(eco):
    calls = {n["id"] for n in eco["nodes"] if n["kind"] == "call"}
    label = {n["id"]: n["label"] for n in eco["nodes"]}
    kind = {n["id"]: n["kind"] for n in eco["nodes"]}
    order, cyc = topo_order(eco)
    f = flows(eco, calls)
    _, pa = _links(eco)
    # A mediator sits between causes and calls: sources (no parents, e.g. the world spheres) are causes, not mediators.
    inner = {n: v for n, v in f.items() if n not in calls and kind[n] != "milestone" and v > 0 and pa.get(n)}
    top = max(inner.values() or [1]) or 1
    mediators = [{"id": n, "label": label[n], "kind": kind[n], "flow": round(v / top, 3)}
                 for n, v in sorted(inner.items(), key=lambda kv: -kv[1])][:10]
    ch, _ = _links(eco)
    conf = []
    for n in order:
        kids = ch.get(n, [])
        if len(kids) < 2:
            continue
        reach_calls = sorted(c for c in _reach(ch, n) if c in calls)
        if len(reach_calls) >= 2 and kind[n] in ("factor", "sphere", "partner", "entity"):
            conf.append({"id": n, "label": label[n], "kind": kind[n], "calls": len(reach_calls), "children": len(kids)})
    rank = {"factor": 0, "sphere": 1, "partner": 2, "entity": 3}
    conf.sort(key=lambda c: (rank[c["kind"]], -c["calls"]))
    return {"dag": not cyc, "cycle": cyc, "mediators": mediators, "confounders": conf[:8]}


def best_path_into(eco, y):
    """Strongest single path from any source into y (max product of weights)."""
    order, cyc = topo_order(eco)
    if cyc:
        return []
    ch, pa = _links(eco)
    best = {n: (1.0, [n]) for n in order if not pa.get(n)}
    for n in order:
        if n in best:
            for c, w in ch.get(n, []):
                cand = (best[n][0] * w, best[n][1] + [c])
                if c not in best or cand[0] > best[c][0]:
                    best[c] = cand
    return best.get(y, (0, []))[1]


def oracle(plan, eco, causal, strategy, ceremonies, today):
    """The oracle's reading for the ceremony: deterministic, from the plan and the causal graph."""
    label = {n["id"]: n["label"] for n in eco["nodes"]}
    now = plan["now"]
    rec = now["recommended"][0] if now["recommended"] else None
    ship = None
    if rec:
        path = best_path_into(eco, "c_" + rec["id"])
        ship = {"id": rec["id"], "name": rec["name"], "grade": rec["grade"], "when": rec["when"],
                "path": [label.get(n, n) for n in path], "pending": rec["pending"]}
    ms = next((m for m in plan["milestones"] if m["status"] != "done"), None)
    med = causal["mediators"][0] if causal["mediators"] else None
    conf = causal["confounders"][0] if causal["confounders"] else None
    late = [r["name"] for r in now["recommended"] if r["when"] == "late"] + [a["name"] for a in now["active"] if a["late"]]
    questions = []
    plans = plan["plans"]
    for k, f in strategy["faces"].items():
        led = [p for p in plans.values() if p["lead"] == k and not (p["archived"] and not p["target_estimated"])]
        led.sort(key=lambda p: -p["score"])
        q = None
        for p in led:
            if p["pending"]:
                mname = next((m["name"] for m in plan["milestones"] if m["id"] == p["pending"][0]), p["pending"][0])
                q = f"¿Quién asume «{mname}» para desbloquear {p['name']} (grado {p['grade']})?"
                break
        if not q and led:
            q = f"¿Iniciamos {led[0]['name']} (grado {led[0]['grade']}) esta quincena?"
        if not q:
            q = f"¿Qué producto de {f['name']} puede reforzar la nave principal?"
        questions.append({"face": k, "name": f["name"], "question": q})
    cadence = strategy.get("ceremony", {}).get("cadence_days", 14)
    last = ceremonies[0] if ceremonies else None
    next_on = (last or {}).get("next_on") or ((_date(last["held_on"]) + dt.timedelta(days=cadence)).isoformat() if last else today.isoformat())
    verdict = []
    if ship:
        verdict.append(f"Perseguir {ship['name']} (grado {ship['grade']})")
    if ms:
        verdict.append(f"mover «{ms['name']}», que desbloquea {len(ms['blocked'])} convocatorias")
    if conf:
        verdict.append(f"y vigilar «{conf['label']}» como confusor al comparar resultados entre convocatorias")
    return {"ship": ship, "milestone": ms and {"id": ms["id"], "name": ms["name"], "weeks": ms["weeks"], "blocked": len(ms["blocked"])},
            "mediator": med, "confounder": conf, "late": late, "questions": questions,
            "verdict": (", ".join(verdict) + ".") if verdict else "Sin naves a la vista.",
            "phases": strategy.get("ceremony", {}).get("phases", []), "cadence_days": cadence,
            "next_on": next_on, "due": next_on <= today.isoformat(), "ceremonies": list(ceremonies)[:12]}


def graph_export(plan, opps=None, eco=None):
    """The ecosystem (same nodes and relations as the 2D view) as graphify graph.json for the 3D viewer.

    Group (colour) = face name, or "Hitos"; community_name = status; relation carries the weight.
    All text is folded to plain ASCII (accents break in the viewer).
    """
    eco = eco or plan["ecosystem"]
    faces = plan["faces"]
    st_es = {"exists": "existe", "planned": "por crear", "proposed": "propuesta", "external": "socio externo", "factor": "factor de contexto"}
    kind_es = {"entity": "figura legal", "face": "cara", "product": "producto", "partner": "socio", "factor": "factor",
               "milestone": "hito", "call": "convocatoria", "sphere": "esfera", "funder": "financiador"}
    nodes = []
    for n in eco["nodes"]:
        st = st_es.get(n["status"], n["status"])
        if n.get("pending"):
            st = "socio externo · relación por crear"
        if n["kind"] == "call":
            st = f"convocatoria · grado {n['grade']}" + (" · próxima edición" if n["status"] == "planned" else "")
            label = f"[{n['grade']}] {n['label']}"
        elif n["kind"] == "milestone":
            st = "hito " + ("hecho" if n["status"] == "exists" else "pendiente")
            label = n["label"]
        else:
            label = n["label"] + ("" if n["status"] in ("exists", "external", "factor") else f" ({st})")
        group = (faces[n["face"]]["name"] if n.get("face") in faces else
                 {"sphere": "Esferas", "funder": "Financiadores"}.get(n["kind"], "Hitos"))
        nodes.append({"id": n["id"], "label": ascii_text(label), "source_file": ascii_text(f"{group}/{kind_es[n['kind']]}"),
                      "source_location": ascii_text(kind_es[n["kind"]]), "community_name": ascii_text(st)})
    links = [{"source": e["from"], "target": e["to"], "weight": e.get("weight", 0.5),
              "relation": ascii_text(f"{e['label']} (peso {e.get('weight', 0.5):.2f})"),
              "confidence": "EXTRACTED" if e["status"] == "exists" else "INFERRED"} for e in eco["edges"]]
    return {"directed": True, "nodes": nodes, "links": links}


def gantt(opps, plans, milestones, today, months_ahead=12):
    """Rows for the Gantt chart: milestones (work → need date) and calls (prep → deadline)."""
    start = (today.replace(day=1) - dt.timedelta(days=1)).replace(day=1)  # previous month
    end = today + dt.timedelta(days=31 * months_ahead)
    rows = []
    for m in milestones:
        if m["status"] == "done":
            continue
        need = sorted(d for d in (plans[i]["milestones_by"] for i in m["blocked"]) if d)
        if not need:
            continue
        future = [d for d in need if d >= today.isoformat()]
        if future:
            due = _date(future[0])
            begin = due - dt.timedelta(weeks=m["weeks"])
            note = f"~{m['weeks']} semanas · necesario el {due.strftime('%d/%m/%Y')}"
        else:  # every need date has passed: show what starting today would give
            begin, due = today, today + dt.timedelta(weeks=m["weeks"])
            note = f"~{m['weeks']} semanas · ya va tarde: si empieza hoy, listo el {due.strftime('%d/%m/%Y')}"
        rows.append({"kind": "milestone", "id": m["id"], "name": m["name"], "face": None,
                     "start": begin.isoformat(), "end": due.isoformat(), "late": begin < today or not future,
                     "status": m["status"], "detail": note + f" · bloquea {len(m['blocked'])} convocatorias"})
    names = {o["id"]: o for o in opps}
    calls = []
    for oid, p in plans.items():
        if not p["target"] or not p["start_by"] or (p["archived"] and not p["target_estimated"]):
            continue
        tgt = _date(p["target"])
        if tgt < today or _date(p["start_by"]) > end:
            continue
        o = names[oid]
        opens = o.get("opens") if not p["target_estimated"] else None
        calls.append({"kind": "call", "id": oid, "name": p["name"], "face": p["lead"], "grade": p["grade"],
                      "start": p["start_by"], "end": p["target"], "opens": opens,
                      "estimated": p["target_estimated"], "late": p["start_by"] < today.isoformat(),
                      "pending": p["pending"], "detail": o.get("amount_text") or ""})
    calls.sort(key=lambda r: (r["end"], r["start"]))
    rows.sort(key=lambda r: r["end"])
    return {"from": start.isoformat(), "to": end.isoformat(), "rows": rows + calls}


def check(strategy, opp_ids):
    """Consistency problems between strategy.json and the tracked opportunities."""
    problems = []
    faces, ents, ms = strategy["faces"], strategy["entities"], strategy["milestones"]
    for oid, st in strategy["opportunities"].items():
        if oid not in opp_ids:
            problems.append(f"strategy: unknown opportunity {oid}")
        for e in st.get("applicant", []):
            if e not in ents:
                problems.append(f"strategy {oid}: unknown entity {e}")
        for f in [st.get("lead")] + st.get("support", []):
            if f not in faces:
                problems.append(f"strategy {oid}: unknown face {f}")
        for m in st.get("prereqs", []):
            if m not in ms:
                problems.append(f"strategy {oid}: unknown milestone {m}")
        if st.get("level") not in (1, 2, 3):
            problems.append(f"strategy {oid}: level must be 1, 2 or 3")
    for eid, e in ents.items():
        for m in e.get("requires", []):
            if m not in ms:
                problems.append(f"strategy entity {eid}: unknown milestone {m}")
    g = strategy.get("graph", {})
    nodes = {n["id"] for n in g.get("nodes", [])}
    calls = {"c_" + i for i in opp_ids}
    for n in g.get("nodes", []):
        if n.get("status") not in NODE_STATUSES:
            problems.append(f"graph node {n['id']}: bad status {n.get('status')}")
        if n.get("face") not in faces and not (n.get("type") in WORLD_KINDS and n.get("face") is None):
            problems.append(f"graph node {n['id']}: unknown face {n.get('face')}")
    for e in g.get("edges", []):
        if e["from"] not in nodes or (e["to"] not in nodes and e["to"] not in calls):
            problems.append(f"graph edge {e['from']}->{e['to']}: unknown node")
        if e.get("status") not in ("exists", "planned"):
            problems.append(f"graph edge {e['from']}->{e['to']}: status must be exists|planned")
        if not 0 <= e.get("weight", 0.5) <= 1:
            problems.append(f"graph edge {e['from']}->{e['to']}: weight must be 0..1")
    sphere_ids = {n["id"] for n in g.get("nodes", []) if n.get("type") == "sphere"}
    funded = set()
    for f in strategy.get("funders", []):
        funded.update(f.get("calls", []))
        for sph, w in f.get("spheres", {}).items():
            if sph not in sphere_ids:
                problems.append(f"funder {f['id']}: {sph} is not a sphere node")
            if not 0 <= w <= 1:
                problems.append(f"funder {f['id']}: weight must be 0..1")
        for c in f.get("calls", []):
            if c not in opp_ids:
                problems.append(f"funder {f['id']}: unknown call {c}")
        if f.get("node") and f["node"] not in nodes:
            problems.append(f"funder {f['id']}: node {f['node']} missing in graph")
    # Every call worth pursuing must hang from its funder (the world surface of the graph).
    for oid in strategy["opportunities"]:
        if oid not in funded:
            problems.append(f"strategy {oid}: no funder (add it to a strategy.json -> funders entry's calls)")
    for oid, st in strategy["opportunities"].items():
        for pr in st.get("products", []):
            if pr not in nodes:
                problems.append(f"strategy {oid}: unknown product node {pr}")
    for k, v in list(ENTITY_NODE.items()) + list(FACE_NODE.items()):
        if v not in nodes:
            problems.append(f"planner mapping {k}: graph node {v} missing")
    # The ecosystem must stay a DAG (causal analysis needs it): static edges + every call's relations.
    fn, fe = funder_edges(strategy)
    eco = {"nodes": [{"id": n} for n in nodes | calls | {"m_" + m for m in ms} | {x["id"] for x in fn}],
           "edges": list(g.get("edges", [])) + fe}
    for oid, st in strategy["opportunities"].items():
        for a in st.get("applicant", [])[:1]:
            eco["edges"] += _call_edges(oid, st, a)
    eco["edges"] = [e for e in eco["edges"] if e["from"] in {n["id"] for n in eco["nodes"]}]
    _, cyc = topo_order(eco)
    if cyc:
        problems.append(f"graph has a cycle among: {', '.join(sorted(cyc)[:8])}")
    for mid, m in ms.items():
        if m.get("status") not in ("pending", "doing", "done"):
            problems.append(f"strategy milestone {mid}: status must be pending|doing|done")
    return problems
