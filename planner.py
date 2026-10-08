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


def make_plan(opps, strategy, applications=(), today=None):
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
    return {
        "today": today.isoformat(), "now": now, "calendar": events,
        "project": strategy["project"], "faces": strategy["faces"], "entities": strategy["entities"],
        "levels": strategy["levels"], "stages": strategy["stages"],
        "municipalities": strategy["municipalities"], "graph": strategy.get("graph", {}),
        "milestones": ms, "plans": plans, "ranking": [p["id"] for p in ranking],
        "applications": list(applications),
    }


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


def graph_export(plan, opps, include_calls=True):
    """Ecosystem as a graphify-style graph.json (nodes + links) for the 3D viewer.

    source_file's first segment is the colour group (the face, or "Hitos");
    community_name carries the existence status; source_location the node kind.
    """
    faces = plan["faces"]
    status_es = {"exists": "existe", "planned": "por crear", "proposed": "propuesta", "external": "socio externo"}
    g = plan.get("graph", {})
    linked = {x for e in g.get("edges", []) if e["status"] == "exists" for x in (e["from"], e["to"])}
    nodes, links = [], []

    def node(nid, label, face_or_group, kind, status):
        group = faces[face_or_group]["name"] if face_or_group in faces else face_or_group
        nodes.append({"id": nid, "label": label, "source_file": f"{group}/{kind}",
                      "source_location": kind, "community_name": status})

    kinds = {"entity": "figura legal", "face": "cara", "product": "producto", "partner": "socio"}
    for n in g.get("nodes", []):
        st = status_es.get(n["status"], n["status"])
        if n["status"] == "external" and n["id"] not in linked:
            st = "socio externo · relación por crear"
        label = n["label"] + ("" if n["status"] in ("exists", "external") else f" ({st})")
        node(n["id"], label, n["face"], kinds.get(n["type"], n["type"]), st)
    for e in g.get("edges", []):
        links.append({"source": e["from"], "target": e["to"], "relation": e["label"],
                      "confidence": "EXTRACTED" if e["status"] == "exists" else "INFERRED"})

    if include_calls:
        # Map strategy entities / faces onto graph nodes so calls hang off the right places.
        ent_node = {"fundacion_dl": "e_fund", "asoc_local": "e_asoc", "semf_asoc": "e_semf",
                    "causality_ent": "e_cg", "branchout_sl": "e_bo", "partner_csic": "x_csic",
                    "partner_uned": "x_uned", "partner_research": "x_idiphisa"}
        face_node = {"cloudy": "f_cloudy", "semf": "f_semf", "causality": "f_cg", "branchout": "f_bo", "delfina": "e_fund"}
        names = {o["id"]: o["name"] for o in opps}
        used_ms = set()
        for oid in plan["ranking"]:
            p = plan["plans"][oid]
            if p["archived"] and not p["target_estimated"]:
                continue
            node("c_" + oid, f"[{p['grade']}] {names.get(oid, oid)}", p["lead"], "convocatoria",
                 f"convocatoria · grado {p['grade']}" + (" · próxima edición" if p["target_estimated"] else ""))
            if ent_node.get(p["applicant"]):
                links.append({"source": "c_" + oid, "target": ent_node[p["applicant"]], "relation": "solicita"})
            for f in p["faces"]:
                if face_node.get(f) and face_node[f] != ent_node.get(p["applicant"]):
                    links.append({"source": "c_" + oid, "target": face_node[f], "relation": "aporta valor"})
            for m in p["pending"]:
                used_ms.add(m)
                links.append({"source": "m_" + m, "target": "c_" + oid, "relation": "bloquea"})
        for m in plan["milestones"]:
            if m["id"] in used_ms:
                node("m_" + m["id"], m["name"], "Hitos", "hito", "hito " + {"pending": "pendiente", "doing": "en curso", "done": "hecho"}[m["status"]])
    for n in nodes:
        for k in ("label", "source_file", "source_location", "community_name"):
            n[k] = ascii_text(n[k])
    for l in links:
        if "relation" in l:
            l["relation"] = ascii_text(l["relation"])
    ids = {n["id"] for n in nodes}
    return {"directed": True, "nodes": nodes,
            "links": [l for l in links if l["source"] in ids and l["target"] in ids]}


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
    for n in g.get("nodes", []):
        if n.get("status") not in ("exists", "planned", "proposed", "external"):
            problems.append(f"graph node {n['id']}: bad status {n.get('status')}")
        if n.get("face") not in faces:
            problems.append(f"graph node {n['id']}: unknown face {n.get('face')}")
    for e in g.get("edges", []):
        if e["from"] not in nodes or e["to"] not in nodes:
            problems.append(f"graph edge {e['from']}->{e['to']}: unknown node")
        if e.get("status") not in ("exists", "planned"):
            problems.append(f"graph edge {e['from']}->{e['to']}: status must be exists|planned")
    for mid, m in ms.items():
        if m.get("status") not in ("pending", "doing", "done"):
            problems.append(f"strategy milestone {mid}: status must be pending|doing|done")
    return problems
