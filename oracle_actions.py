"""money-radar: acciones e intents locales del oráculo por voz.

- `state()`: el mismo payload que build.py inyecta en el dashboard (para refrescar la web).
- `ACTIONS`: acciones que cambian el estado. Cada una tiene `preview(args)` (frase hablada
  que se confirma) y `run(args)`. Reutilizan los CLI existentes (orchestrator.py, oraculo.py)
  o editan strategy.json / build.py de forma transaccional: copia de seguridad, monitor + build,
  y restauración si algo falla. Ninguna presenta solicitudes, toca git ni borra entradas.
- `intent(text, ctx)`: órdenes conocidas en español, resueltas en local; None si no encaja
  (entonces el servidor pregunta a Claude).

Lo carga oracle_server.py por ruta (python3 -I no incluye la carpeta del script en sys.path).
"""

import datetime as dt
import difflib
import importlib.util
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = [sys.executable, "-I"]
TABS = {"radar": "radar", "estrategia": "strategy", "calendario": "calendar", "gantt": "calendar",
        "grafo": "graph", "oraculo": "oracle", "madrid oeste": "west", "municipios": "west"}
FACE_NODE = {"cloudy": "f_cloudy", "semf": "f_semf", "causality": "f_cg", "branchout": "f_bo", "delfina": "e_fund"}
MONTHS = {m: i for i, m in enumerate(["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
                                      "septiembre", "octubre", "noviembre", "diciembre"], 1)}
NUM = {"un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8,
       "nueve": 9, "diez": 10, "doce": 12, "quince": 15}
MS_STATUS = {"hecho": "hecho", "hecha": "hecho", "terminado": "hecho", "completado": "hecho",
             "en curso": "en-curso", "empezado": "en-curso", "pendiente": "pendiente"}


def _load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def norm(text):
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s.,:|/-]", " ", t)).strip()


# ---------------------------------------------------------------- state
def state():
    build, planner = _load("build"), _load("planner")
    conn = sqlite3.connect(HERE / "money.db")
    conn.row_factory = sqlite3.Row
    opps, changes = build.load(conn)
    apps = [dict(r) for r in conn.execute("SELECT * FROM applications ORDER BY updated_on DESC")]
    cers = [dict(r, decisions=json.loads(r["decisions"]), commitments=json.loads(r["commitments"]))
            for r in conn.execute("SELECT * FROM ceremonies ORDER BY held_on DESC, id DESC")]
    conn.close()
    plan = planner.make_plan(opps, planner.load_strategy(), apps, dt.date.today(), cers)
    return {"today": dt.date.today().isoformat(), "lines": build.LINES, "opportunities": opps,
            "changes": changes, "lessons": build.read_lessons(), "plan": plan}


def catalog(st):
    """(kind, id, label) for everything the user can name."""
    plan = st["plan"]
    out = [("call", o["id"], o["name"]) for o in st["opportunities"]]
    out += [("milestone", m["id"], m["name"]) for m in plan["milestones"]]
    out += [("node", n["id"], n["label"]) for n in plan["ecosystem"]["nodes"] if n["kind"] not in ("call", "milestone")]
    out += [("face", k, f["name"]) for k, f in plan["faces"].items()]
    return out


STOP = {"de", "la", "el", "los", "las", "del", "a", "al", "y", "en", "para", "por", "con", "que", "un", "una", "me", "mi"}


def find(text, st, kinds=None):
    """Best fuzzy match for a spoken name: word overlap first, then difflib similarity."""
    q = [w for w in norm(text).split() if w not in STOP]
    if not q:
        return None
    best, score = None, 0.0
    for kind, cid, label in catalog(st):
        if kinds and kind not in kinds:
            continue
        words = set(norm(label + " " + cid.replace("-", " ").replace("_", " ")).split()) - STOP
        overlap = sum(1 for w in q if w in words or any(x.startswith(w) and len(w) > 3 for x in words))
        sim = difflib.SequenceMatcher(None, " ".join(q), norm(label)).ratio()
        s = overlap / len(q) + 0.5 * sim
        if s > score:
            best, score = (kind, cid, label), s
    return best if score >= 0.55 else None


def parse_date(text, today=None):
    """'2026-11-15', '15 de noviembre [de 2026]', 'en 3 meses|semanas|dias', 'manana', '15/11/2026'."""
    today = today or dt.date.today()
    t = norm(text)
    if m := re.search(r"(\d{4})-(\d{2})-(\d{2})", t):
        return dt.date(*map(int, m.groups()))
    if m := re.search(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", t):
        d, mo, y = int(m[1]), int(m[2]), m[3]
        y = int(y) + (2000 if y and len(y) == 2 else 0) if y else today.year
        out = dt.date(y, mo, d)
        return out if m[3] or out >= today else out.replace(year=out.year + 1)
    if m := re.search(r"(\d{1,2}|" + "|".join(NUM) + r") de (" + "|".join(MONTHS) + r")(?: de (\d{4}))?", t):
        d = int(m[1]) if m[1].isdigit() else NUM[m[1]]
        out = dt.date(int(m[3]) if m[3] else today.year, MONTHS[m[2]], d)
        return out if m[3] or out >= today else out.replace(year=out.year + 1)
    if m := re.search(r"en (\d+|" + "|".join(NUM) + r") (dia|dias|semana|semanas|mes|meses)", t):
        n = int(m[1]) if m[1].isdigit() else NUM[m[1]]
        days = {"dia": 1, "dias": 1, "semana": 7, "semanas": 7, "mes": 30, "meses": 30}[m[2]] * n
        return today + dt.timedelta(days=days)
    if "pasado manana" in t:
        return today + dt.timedelta(days=2)
    if "manana" in t:
        return today + dt.timedelta(days=1)
    return None


# ---------------------------------------------------------------- transactional helpers
def _cli(*args):
    r = subprocess.run(PY + [str(HERE / args[0])] + list(args[1:]), cwd=HERE, capture_output=True, text=True)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def _transaction(mutate):
    """Back up build.py and strategy.json, mutate, validate (monitor + build), restore on failure."""
    tmp = Path(tempfile.mkdtemp(prefix="oraculo-"))
    files = [HERE / "build.py", HERE / "strategy.json"]
    for f in files:
        shutil.copy2(f, tmp / f.name)
    try:
        mutate()
        ok, out = _cli("monitor.py")
        if ok:
            ok, out = _cli("build.py")
        if not ok:
            raise RuntimeError(out.splitlines()[-1] if out else "validación fallida")
        return out
    except Exception:
        for f in files:
            shutil.copy2(tmp / f.name, f)
        _cli("build.py")
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _strategy():
    return json.loads((HERE / "strategy.json").read_text(encoding="utf-8"))


def _save_strategy(s):
    (HERE / "strategy.json").write_text(json.dumps(s, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _label(st, nid):
    return next((n["label"] for n in st["plan"]["ecosystem"]["nodes"] if n["id"] == nid), nid)


# ---------------------------------------------------------------- actions
def _p_hito(a, st):
    name = next((m["name"] for m in st["plan"]["milestones"] if m["id"] == a["milestone"]), a["milestone"])
    return f"Marcar el hito «{name}» como {a['status'].replace('-', ' ')}."


def _r_hito(a):
    ok, out = _cli("orchestrator.py", "hito", a["milestone"], a["status"])
    if not ok:
        raise RuntimeError(out)
    return out


def _p_iniciar(a, st):
    return f"Iniciar la solicitud de {_opp_name(st, a['id'])}" + (f" con la entidad {a['entidad']}" if a.get("entidad") else "") + "."


def _r_iniciar(a):
    args = ["orchestrator.py", "iniciar", a["id"]]
    if a.get("entidad"):
        args += ["--entidad", a["entidad"]]
    if a.get("nivel"):
        args += ["--nivel", str(a["nivel"])]
    ok, out = _cli(*args)
    if not ok:
        raise RuntimeError(out)
    return out.splitlines()[0]


def _p_avanzar(a, st):
    return f"Avanzar la solicitud de {_opp_name(st, a['id'])} a la siguiente etapa" + (f", con la nota «{a['nota']}»" if a.get("nota") else "") + "."


def _r_avanzar(a):
    args = ["orchestrator.py", "avanzar", a["id"]] + (["--nota", a["nota"]] if a.get("nota") else [])
    ok, out = _cli(*args)
    if not ok:
        raise RuntimeError(out)
    return out.splitlines()[0]


def _p_registrar(a, st):
    return (f"Registrar la ceremonia con {len(a.get('decisiones', []))} decisiones y "
            f"{len(a.get('compromisos', []))} compromisos" + (f"; próxima el {a['proxima']}" if a.get("proxima") else "") + ".")


def _r_registrar(a):
    if not a.get("decisiones"):
        raise RuntimeError("Hace falta al menos una decisión.")
    args = ["oraculo.py", "registrar"]
    if a.get("resumen"):
        args += ["--resumen", a["resumen"]]
    for d in a["decisiones"]:
        args += ["--decision", d]
    for c in a.get("compromisos", []):
        args += ["--compromiso", f"{c['rol']}|{c['tarea']}|{c['fecha']}"]
    if a.get("proxima"):
        args += ["--proxima", a["proxima"]]
    ok, out = _cli(*args)
    if not ok:
        raise RuntimeError(out)
    return out


def _p_peso(a, st):
    return f"Cambiar el peso de «{_label(st, a['from'])}» → «{_label(st, a['to'])}» a {float(a['weight']):.2f}."


def _r_peso(a):
    w = float(a["weight"])
    if not 0 <= w <= 1:
        raise RuntimeError("El peso debe estar entre 0 y 1.")

    def mutate():
        s = _strategy()
        e = next((e for e in s["graph"]["edges"] if e["from"] == a["from"] and e["to"] == a["to"]), None)
        if not e:
            raise RuntimeError("Esa relación no está en strategy.json → graph. Las relaciones de cada convocatoria "
                               "(solicita, lidera, evidencia, habilita) salen de su estrategia: cambia allí el encaje o los productos.")
        e["weight"] = round(w, 2)
        _save_strategy(s)
    _transaction(mutate)
    return f"Peso actualizado a {w:.2f}; plan y dashboard recalculados."


def _p_relacion(a, st):
    return (f"Añadir la relación «{_label(st, a['from'])}» → «{_label(st, a['to'])}» ({a.get('label', 'contribuye')}), "
            f"peso {float(a.get('weight', 0.5)):.2f}, {('ya existente' if a.get('status') == 'exists' else 'por crear')}.")


def _r_relacion(a):
    def mutate():
        s = _strategy()
        if any(e["from"] == a["from"] and e["to"] == a["to"] for e in s["graph"]["edges"]):
            raise RuntimeError("Esa relación ya existe; cambia su peso en su lugar.")
        s["graph"]["edges"].append({"from": a["from"], "to": a["to"], "label": a.get("label", "contribuye"),
                                    "status": a.get("status", "planned"), "weight": round(float(a.get("weight", 0.5)), 2)})
        _save_strategy(s)
    _transaction(mutate)
    return "Relación añadida; el grafo sigue siendo un DAG."


def _p_estado_nodo(a, st):
    return f"Cambiar el estado de «{_label(st, a['node'])}» a {a['status']}."


def _r_estado_nodo(a):
    def mutate():
        s = _strategy()
        n = next((n for n in s["graph"]["nodes"] if n["id"] == a["node"]), None)
        if not n:
            raise RuntimeError("Ese nodo no está en strategy.json → graph.")
        n["status"] = a["status"]
        _save_strategy(s)
    _transaction(mutate)
    return "Estado actualizado."


def _p_archivar(a, st):
    return (f"Archivar {_opp_name(st, a['id'])} porque {a['reason']}; revisar el {a['review_on']}. "
            "No se borra: queda en el archivo.")


def _r_archivar(a):
    dt.date.fromisoformat(a["review_on"])

    def mutate():
        p = HERE / "build.py"
        src = p.read_text(encoding="utf-8")
        i = src.find(f'id="{a["id"]}"')
        if i < 0:
            raise RuntimeError("No encuentro esa convocatoria en ROWS.")
        j = src.find("\n    ),", i)
        if "archived=True" in src[i:j]:
            raise RuntimeError("Ya está archivada.")
        ins = (f'\n        archived=True,\n        archive_reason={json.dumps(a["reason"], ensure_ascii=False)},'
               f'\n        review_on="{a["review_on"]}",')
        p.write_text(src[:j] + ins + src[j:], encoding="utf-8")
    _transaction(mutate)
    return "Archivada con motivo y fecha de revisión."


def _opp_name(st, oid):
    return next((o["name"] for o in st["opportunities"] if o["id"] == oid), oid)


ACTIONS = {
    "hito": {"params": ["milestone", "status"], "preview": _p_hito, "run": _r_hito},
    "iniciar": {"params": ["id"], "preview": _p_iniciar, "run": _r_iniciar},
    "avanzar": {"params": ["id"], "preview": _p_avanzar, "run": _r_avanzar},
    "registrar_ceremonia": {"params": ["decisiones"], "preview": _p_registrar, "run": _r_registrar},
    "peso": {"params": ["from", "to", "weight"], "preview": _p_peso, "run": _r_peso},
    "relacion": {"params": ["from", "to"], "preview": _p_relacion, "run": _r_relacion},
    "estado_nodo": {"params": ["node", "status"], "preview": _p_estado_nodo, "run": _r_estado_nodo},
    "archivar": {"params": ["id", "reason", "review_on"], "preview": _p_archivar, "run": _r_archivar},
}


def validate(action_id, args):
    if action_id not in ACTIONS:
        return f"Acción desconocida «{action_id}»."
    miss = [p for p in ACTIONS[action_id]["params"] if args.get(p) in (None, "", [])]
    return f"Faltan datos: {', '.join(miss)}." if miss else None


# ---------------------------------------------------------------- local intents
def _reply(say, view=None, options=None, action=None, **extra):
    return {"say": say, "view": view, "options": options or [], "action": action, "source": "local", **extra}


def _view_for(kind, cid, tab=None):
    if kind == "call":
        return {"tab": tab or "radar", "focus": {"kind": "call", "id": cid}}
    if kind == "milestone":
        return {"tab": tab or "calendar", "focus": {"kind": "milestone", "id": cid}}
    if kind == "face":
        return {"tab": "graph", "focus": {"kind": "node", "id": FACE_NODE.get(cid, cid)}}
    return {"tab": "graph", "focus": {"kind": "node", "id": cid}}


def intent(text, ctx, st):
    t = norm(text)
    plan = st["plan"]
    capture = (ctx.get("ceremony") or {}).get("capture")

    # Ceremony dictation: "rol, tarea, fecha"
    if capture == "commitment":
        if re.fullmatch(r"(siguiente|ya esta|ya|nada mas|termina|terminar|terminado|fin|listo)( compromisos)?", t):
            return _reply("Cerramos los compromisos.", ceremony="next")
        parts = [p.strip() for p in re.split(r"[,;|]", text) if p.strip()]
        date = parse_date(parts[-1]) if len(parts) >= 3 else None
        if len(parts) < 3 or not date:
            return _reply("No lo he entendido. Dime rol, tarea y fecha, por ejemplo: Fundación, pedir cita con Cultura, 30 de octubre.")
        c = {"rol": parts[0], "tarea": ", ".join(parts[1:-1]), "fecha": date.isoformat()}
        return _reply(f"Anotado: {c['rol']}, {c['tarea']}, para el {date.strftime('%d/%m/%Y')}. ¿Otro compromiso?",
                      commitment=c, options=[{"label": "Terminar compromisos", "ceremony": "next"}])

    # Ceremony control
    if re.search(r"\b(empieza|empezar|inicia|iniciar|comienza|comenzar|abre|arranca)\b.*\bceremonia\b", t):
        return _reply("Abro la ceremonia.", ceremony="start")
    if re.fullmatch(r"(siguiente|siguiente fase|continua|continuar|adelante con la ceremonia)", t):
        return _reply("Siguiente fase.", ceremony="next")

    # Navigation
    if m := re.search(r"\b(abre|ve a|vamos a|ir a|ensename|muestrame|llevame a|pestana)\b.*\b(radar|estrategia|calendario|gantt|grafo|oraculo|madrid oeste|municipios)\b", t):
        tab = TABS[m.group(2)]
        view = {"tab": tab}
        if m.group(2) == "gantt":
            view["focus"] = {"kind": "gantt"}
        if "3d" in t and tab == "graph":
            view["mode"] = "3d"
        return _reply(f"Abro {m.group(2)}.", view=view)

    if re.search(r"\b(que hago ahora|que toca|que hacemos|por donde empiezo|estado general)\b", t):
        now = plan["now"]
        rec = now["recommended"][0] if now["recommended"] else None
        ms = next((m for m in plan["milestones"] if m["status"] != "done"), None)
        say = []
        if now["active"]:
            say.append(f"Tienes {len(now['active'])} solicitudes en curso.")
        if ms:
            say.append(f"El hito que más desbloquea es {ms['name']}.")
        if rec:
            say.append(f"La mejor convocatoria para empezar es {rec['name']}, grado {rec['grade']}.")
        opts = []
        if ms:
            opts.append({"label": f"Marcar «{ms['name']}» en curso", "action": {"id": "hito", "args": {"milestone": ms["id"], "status": "en-curso"}}})
        if rec:
            opts.append({"label": f"Ver {rec['name'][:40]}", "utterance": f"muéstrame {rec['name']}"})
            opts.append({"label": "Iniciar esa solicitud", "action": {"id": "iniciar", "args": {"id": rec["id"]}}})
        return _reply(" ".join(say) or "No hay nada urgente.", view={"tab": "strategy", "focus": {"kind": "now"}}, options=opts)

    if re.search(r"\b(dictamen|lectura del oraculo|que dice el oraculo)\b", t):
        o = plan["oracle"]
        return _reply(o["verdict"], view={"tab": "oracle", "focus": {"kind": "phase", "id": "dictamen"}},
                      options=[{"label": "Empezar la ceremonia", "ceremony": "start"}])

    # Milestone status
    if m := re.search(r"\bmarca(r)?\b (?:el hito )?(.+?) como (hecho|hecha|terminado|completado|en curso|empezado|pendiente)", t):
        hit = find(m.group(2), st, {"milestone"})
        if not hit:
            return _reply(f"No encuentro el hito «{m.group(2)}». ¿Cuál de estos?", view={"tab": "strategy"},
                          options=[{"label": x["name"], "utterance": f"marca {x['name']} como {m.group(3)}"} for x in plan["milestones"][:5]])
        action = {"id": "hito", "args": {"milestone": hit[1], "status": MS_STATUS[m.group(3)]}}
        return _reply("Te pido confirmación.", view=_view_for("milestone", hit[1], "strategy"), action=action)

    # Weight change
    if m := re.search(r"\b(pon|cambia|sube|baja|ajusta)\b el peso de (.+?) (?:a|hacia|sobre) (.+?) (?:a|en|hasta) (\d+(?:[.,]\d+)?)", t):
        a, b = find(m.group(2), st, {"node", "face", "call", "milestone"}), find(m.group(3), st, {"node", "face", "call", "milestone"})
        if not a or not b:
            return _reply("No identifico los dos nodos de esa relación. Dímelos con su nombre del grafo.", view={"tab": "graph"})
        ida = FACE_NODE.get(a[1], a[1]) if a[0] == "face" else ("c_" + a[1] if a[0] == "call" else ("m_" + a[1] if a[0] == "milestone" else a[1]))
        idb = FACE_NODE.get(b[1], b[1]) if b[0] == "face" else ("c_" + b[1] if b[0] == "call" else ("m_" + b[1] if b[0] == "milestone" else b[1]))
        w = float(m.group(4).replace(",", "."))
        w = w / 10 if w > 1 else w
        return _reply("Te pido confirmación.", view={"tab": "graph", "focus": {"kind": "node", "id": ida}},
                      action={"id": "peso", "args": {"from": ida, "to": idb, "weight": w}})

    # Applications
    if m := re.search(r"\b(inicia|iniciar|empieza|empezar|arranca)\b (?:la )?(?:solicitud )?(?:de |a )?(.+)", t):
        hit = find(m.group(2), st, {"call"})
        if hit:
            return _reply("Te pido confirmación.", view=_view_for("call", hit[1]), action={"id": "iniciar", "args": {"id": hit[1]}})
    if m := re.search(r"\b(avanza|avanzar|pasa)\b (?:la )?(?:solicitud )?(?:de )?(.+?)(?: con (?:la )?nota (.+))?$", t):
        hit = find(m.group(2), st, {"call"})
        if hit:
            args = {"id": hit[1]}
            if m.group(3):
                args["nota"] = m.group(3)
            return _reply("Te pido confirmación.", view={"tab": "strategy"}, action={"id": "avanzar", "args": args})

    # Archive
    if m := re.search(r"\barchiva(r)?\b (.+?) porque (.+?)(?:,? (?:y )?revisa(?:r|rla|rlo)? (?:el |en )?(.+))?$", t):
        hit = find(m.group(2), st, {"call"})
        review = parse_date(m.group(4) or "") or (dt.date.today() + dt.timedelta(days=90))
        orig = re.search(r"porque (.+?)(?:,? (?:y )?revis\w* .*)?$", text, re.I)  # keep accents in the reason
        reason = (orig.group(1) if orig else m.group(3)).strip().rstrip(".,")
        if hit:
            return _reply("Te pido confirmación.", view=_view_for("call", hit[1]),
                          action={"id": "archivar", "args": {"id": hit[1], "reason": reason, "review_on": review.isoformat()}})

    # Causal analysis
    if m := re.search(r"\banaliza\b (?:el efecto de )?(.+?) sobre (.+)", t):
        a, b = find(m.group(1), st), find(m.group(2), st)
        if a and b:
            planner = _load("planner")
            def nid(x):
                return FACE_NODE.get(x[1], x[1]) if x[0] == "face" else ("c_" + x[1] if x[0] == "call" else ("m_" + x[1] if x[0] == "milestone" else x[1]))
            r = planner.analyze(plan["ecosystem"], nid(a), nid(b))
            med = ", ".join(_label(st, n) for n, _ in r["mediators"][:2]) or "ninguno"
            adj = ", ".join(_label(st, n) for n in r["adjust"])
            return _reply(f"Efecto total {r['total']:.2f}: directo {r['direct']:.2f} e indirecto {r['indirect']:.2f}, por {r['n_paths']} caminos. "
                          f"Mediadores principales: {med}. "
                          + (f"Para comparar sin sesgo, ajusta por {adj}." if adj else "No hace falta ajustar por ningún confusor."),
                          view={"tab": "graph", "focus": {"kind": "node", "id": nid(a)}})

    # Show something
    if m := re.search(r"\b(muestrame|ensename|ensena|busca|donde esta|hablame de|que es|ver)\b (.+)", t):
        hit = find(m.group(2), st)
        if hit:
            kind, cid, label = hit
            return _reply(f"Aquí tienes {label}.", view=_view_for(kind, cid))
    return None
