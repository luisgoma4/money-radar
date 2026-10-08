"""money-radar: orquestador de solicitudes de becas y ayudas.

Guía cada solicitud por las etapas definidas en strategy.json (detectar →
cualificar → estrategia → preparar → redactar → presentar → seguimiento →
cerrar), usando el plan de planner.py: entidad solicitante, nivel de oferta,
caras, hitos pendientes y calendario.

Uso (desde la carpeta del proyecto):
  python3 -I orchestrator.py                      # estado: qué hacer ahora
  python3 -I orchestrator.py plan <id>            # estrategia completa de una convocatoria
  python3 -I orchestrator.py iniciar <id> [--entidad <entidad>] [--nivel 1|2|3]
  python3 -I orchestrator.py avanzar <id> [--nota "texto"]
  python3 -I orchestrator.py hito <M_ID> pendiente|en-curso|hecho
  python3 -I orchestrator.py etapas               # el proceso y sus listas de control

Reglas: la presentación la hace siempre una persona en la sede oficial con su
certificado; este programa nunca envía nada ni guarda datos personales. Las
solicitudes no se borran: se cierran (etapa «cerrar») y quedan en el historial.
Los borradores van a solicitudes/<id>/ (fuera de git: el repositorio es público).
"""

import datetime as dt
import importlib.util
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = HERE / "money.db"
DRAFTS = HERE / "solicitudes"
TODAY = dt.date.today()
MS_STATUS = {"pendiente": "pending", "en-curso": "doing", "hecho": "done"}


def load_planner():
    spec = importlib.util.spec_from_file_location("planner", HERE / "planner.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def connect():
    if not DB.exists():
        sys.exit("No existe money.db: ejecuta antes  python3 -I monitor.py && python3 -I build.py")
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def context():
    planner = load_planner()
    strategy = planner.load_strategy()
    conn = connect()
    opps = [dict(r) for r in conn.execute("SELECT * FROM opportunities")]
    apps = {r["opportunity_id"]: dict(r) for r in conn.execute("SELECT * FROM applications")}
    plan = planner.make_plan(opps, strategy, list(apps.values()), TODAY)
    return planner, strategy, conn, {o["id"]: o for o in opps}, apps, plan


def rebuild():
    subprocess.run([sys.executable, "-I", str(HERE / "build.py")], check=True, stdout=subprocess.DEVNULL)


def fmt(d):
    return dt.date.fromisoformat(d).strftime("%d/%m/%Y") if d else "—"


def ms_name(plan, mid):
    return next((m["name"] for m in plan["milestones"] if m["id"] == mid), mid)


def stage_index(plan, stage):
    return next(i for i, s in enumerate(plan["stages"]) if s["id"] == stage)


# ---------------------------------------------------------------- commands
def cmd_estado(_args):
    _, strategy, _, _, _, plan = context()
    now = plan["now"]
    print(f"ORQUESTADOR · {strategy['project']['name']} · {fmt(TODAY.isoformat())}\n")

    print("1) Solicitudes en curso")
    if not now["active"]:
        print("   Ninguna. Empieza por una de grado A/B de la lista 3.")
    for a in now["active"]:
        print(f"   · {a['name']} — etapa {a['stage_n']}/{a['stages']} «{a['stage_name']}»"
              f"{' · plazo ' + fmt(a['target']) if a['target'] else ''}{' · ¡TARDE!' if a['late'] else ''}")
        for c in a["checklist"]:
            print(f"       □ {c}")

    print("\n2) Hitos a mover ahora (más valor desbloqueado primero)")
    by_id = {m["id"]: m for m in plan["milestones"]}
    for mid in now["milestones"]:
        m = by_id[mid]
        mark = "◐" if m["status"] == "doing" else "○"
        print(f"   {mark} {m['id']}: {m['name']} (~{m['weeks']} sem) → desbloquea {m['unlocks']} puntos en "
              f"{len(m['blocked'])} convocatorias")

    print("\n3) Convocatorias recomendadas (no iniciadas)")
    for r in now["recommended"]:
        when = {"late": f"¡TARDE! plazo {fmt(r['target'])}, había que empezar el {fmt(r['start_by'])}",
                "start_by": f"empezar antes del {fmt(r['start_by'])}",
                "rolling": "abierta todo el año", "tbd": "fechas por anunciar"}[r["when"]]
        block = f" · bloqueada por: {', '.join(r['pending'])}" if r["pending"] else " · lista"
        print(f"   [{r['grade']} {r['score']}] {r['id']} — {when}{block}")
    print("\nSiguiente paso: python3 -I orchestrator.py plan <id>   ·   iniciar <id>")


def cmd_plan(args):
    if not args:
        sys.exit("Uso: plan <id>")
    _, strategy, _, opps, apps, plan = context()
    oid = args[0]
    if oid not in plan["plans"]:
        sys.exit(f"No conozco «{oid}». Ids: {', '.join(sorted(plan['plans']))}")
    p, o = plan["plans"][oid], opps[oid]
    ent = strategy["entities"].get(p["applicant"], {})
    print(f"{p['name']}\n{'=' * min(len(p['name']), 80)}")
    print(f"Grado {p['grade']} ({p['score']}/100) · encaje {p['parts']['fit']} · valor {p['parts']['value']} · "
          f"preparación {p['parts']['readiness']} · calendario {p['parts']['timing']} · estratégico {p['parts']['strategic']}")
    print(f"Financiador: {o['funder']} · {o['scope']} · {o['kind']} · {o['verification']}")
    print(f"Importe: {o['amount_text'] or '—'}")
    print(f"Plazo: {fmt(p['target'])}{' (próxima edición estimada)' if p['target_estimated'] else ''}"
          f" · empezar antes del {fmt(p['start_by'])}"
          + (f" · hitos listos antes del {fmt(p['milestones_by'])}" if p['milestones_by'] else ""))
    print(f"\nSolicita: {ent.get('name', p['applicant'])} ({ent.get('form', '?')}, {ent.get('status', '?')})")
    lvl = strategy["levels"][str(p["level"])]
    print(f"Nivel de oferta {p['level']} · {lvl['name']}: {lvl['detail']}")
    print("Caras: " + " + ".join(strategy["faces"][f]["name"] + (" (lidera)" if f == p["lead"] else "") for f in p["faces"]))
    for f in p["faces"]:
        print(f"   · {strategy['faces'][f]['name']}: {strategy['faces'][f]['offers']}")
    print(f"\nOferta de valor: {p['offer'] or '—'}")
    print(f"Qué pedir: {p['ask'] or '—'}")
    print(f"Base del valor: {p['value_basis']}")
    if o.get("winners"):
        print(f"\nGanadores anteriores: {o['winners']}")
    print("\nHitos previos:" if p["prereqs"] else "\nHitos previos: ninguno")
    for m in p["prereqs"]:
        print(f"   {'✓' if m not in p['pending'] else '○'} {m}: {ms_name(plan, m)}")
    print(f"\nRequisitos: {o['requirements'] or '—'}")
    print(f"Fuente oficial: {o['url']}")
    if oid in apps:
        a = apps[oid]
        print(f"\nSolicitud en curso: etapa «{a['stage']}» desde {fmt(a['updated_on'])}")
    else:
        print(f"\nPara empezar: python3 -I orchestrator.py iniciar {oid}")


def write_plan_md(oid, strategy, plan, opps, app):
    p, o = plan["plans"][oid], opps[oid]
    folder = DRAFTS / oid
    folder.mkdir(parents=True, exist_ok=True)
    stage = app["stage"]
    lines = [f"# {p['name']}", "",
             f"Generado por orchestrator.py el {fmt(TODAY.isoformat())}. Este archivo se regenera; escribe los borradores en otros archivos de esta carpeta.", "",
             f"- Grado: **{p['grade']}** ({p['score']}/100)",
             f"- Solicita: {strategy['entities'][app['applicant']]['name']}",
             f"- Nivel de oferta: {app['level']} · {strategy['levels'][str(app['level'])]['name']}",
             f"- Caras: {', '.join(strategy['faces'][f]['name'] for f in p['faces'])}",
             f"- Plazo: {fmt(p['target'])}{' (estimado)' if p['target_estimated'] else ''} · empezar antes del {fmt(p['start_by'])}",
             f"- Fuente oficial: {o['url']}", "",
             "## Oferta de valor", "", p["offer"] or "—", "", "## Qué pedir", "", p["ask"] or "—", "",
             "## Ganadores anteriores", "", o.get("winners") or "Por investigar (regla de ganadores).", "",
             "## Etapas", ""]
    cur = stage_index(plan, stage)
    for i, st in enumerate(plan["stages"]):
        mark = "x" if i < cur else " "
        lines.append(f"### {i + 1}. {st['name']}{'  ← etapa actual' if i == cur else ''}")
        lines += [f"- [{mark}] {c}" for c in st["checklist"]] + [""]
    (folder / "PLAN.md").write_text("\n".join(lines), encoding="utf-8")
    return folder


def log(conn, oid, field, old, new):
    conn.execute("INSERT INTO changes VALUES (?, ?, ?, ?, ?)", (oid, field, old, new, TODAY.isoformat()))


def opt(args, name, default=None):
    if name in args:
        i = args.index(name)
        return args[i + 1] if i + 1 < len(args) else default
    return default


def cmd_iniciar(args):
    if not args:
        sys.exit("Uso: iniciar <id> [--entidad <entidad>] [--nivel 1|2|3]")
    _, strategy, conn, opps, apps, plan = context()
    oid = args[0]
    if oid not in plan["plans"]:
        sys.exit(f"No conozco «{oid}».")
    if oid in apps:
        sys.exit(f"Ya está iniciada (etapa «{apps[oid]['stage']}»). Usa: avanzar {oid}")
    p = plan["plans"][oid]
    applicant = opt(args, "--entidad", p["applicant"])
    if applicant not in strategy["entities"]:
        sys.exit(f"Entidad desconocida. Opciones: {', '.join(strategy['entities'])}")
    level = int(opt(args, "--nivel", p["level"]))
    today = TODAY.isoformat()
    conn.execute("INSERT INTO applications VALUES (?, 'detectar', ?, ?, ?, ?, NULL)",
                 (oid, applicant, level, today, today))
    log(conn, oid, "application_stage", None, "detectar")
    conn.commit()
    app = dict(conn.execute("SELECT * FROM applications WHERE opportunity_id = ?", (oid,)).fetchone())
    folder = write_plan_md(oid, strategy, plan, opps, app)
    conn.close()
    rebuild()
    print(f"Solicitud iniciada: {p['name']}\nCarpeta de trabajo: {folder.relative_to(HERE)}/PLAN.md")
    if p["pending"]:
        print("Antes de «preparar» hay que completar: " + "; ".join(f"{m} ({ms_name(plan, m)})" for m in p["pending"]))
    print(f"Siguiente: completa la lista de «Detectar» y ejecuta  python3 -I orchestrator.py avanzar {oid}")


def cmd_avanzar(args):
    if not args:
        sys.exit('Uso: avanzar <id> [--nota "texto"]')
    _, strategy, conn, opps, apps, plan = context()
    oid = args[0]
    if oid not in apps:
        sys.exit(f"No hay solicitud iniciada para «{oid}». Usa: iniciar {oid}")
    app = apps[oid]
    i = stage_index(plan, app["stage"])
    if i == len(plan["stages"]) - 1:
        sys.exit("La solicitud ya está cerrada. (Las solicitudes no se borran.)")
    nxt = plan["stages"][i + 1]["id"]
    note = opt(args, "--nota")
    p = plan["plans"][oid]
    if nxt == "preparar" and p["pending"]:
        sys.exit("No se puede pasar a «preparar»: faltan hitos " + ", ".join(p["pending"]) +
                 ". Márcalos con: hito <M_ID> hecho")
    if app["stage"] == "presentar" and not note:
        sys.exit('Para salir de «presentar» indica el justificante de registro: --nota "nº registro …"'
                 " (la presentación la hace la persona responsable en la sede oficial).")
    notes = "; ".join(x for x in [app["notes"], note] if x)
    conn.execute("UPDATE applications SET stage = ?, updated_on = ?, notes = ? WHERE opportunity_id = ?",
                 (nxt, TODAY.isoformat(), notes or None, oid))
    log(conn, oid, "application_stage", app["stage"], nxt)
    conn.commit()
    app = dict(conn.execute("SELECT * FROM applications WHERE opportunity_id = ?", (oid,)).fetchone())
    write_plan_md(oid, strategy, plan, opps, app)
    conn.close()
    rebuild()
    st = plan["stages"][i + 1]
    print(f"{p['name']}: etapa {i + 2}/{len(plan['stages'])} «{st['name']}»")
    for c in st["checklist"]:
        print(f"   □ {c}")
    if nxt == "presentar":
        print("Recuerda: la presenta una persona en la sede oficial con su certificado. Nadie automatiza este paso.")
    if nxt == "cerrar":
        print("Al cerrar: registra los ganadores (python3 -I winners.py <BDNS>), añade la lección a LESSONS.md"
              " y deja la entrada archivada con fecha de revisión.")


def cmd_hito(args):
    if len(args) < 2 or args[1] not in MS_STATUS:
        sys.exit("Uso: hito <M_ID> pendiente|en-curso|hecho")
    path = HERE / "strategy.json"
    strategy = json.loads(path.read_text(encoding="utf-8"))
    mid = args[0]
    if mid not in strategy["milestones"]:
        sys.exit(f"Hito desconocido. Opciones: {', '.join(strategy['milestones'])}")
    old = strategy["milestones"][mid]["status"]
    strategy["milestones"][mid]["status"] = MS_STATUS[args[1]]
    path.write_text(json.dumps(strategy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    conn = connect()
    log(conn, mid, "milestone_status", old, MS_STATUS[args[1]])
    conn.commit()
    conn.close()
    rebuild()
    print(f"{mid}: {old} → {MS_STATUS[args[1]]}. Plan y dashboard recalculados.")


def cmd_etapas(_args):
    _, _, _, _, _, plan = context()
    for i, st in enumerate(plan["stages"]):
        print(f"{i + 1}. {st['name']}")
        for c in st["checklist"]:
            print(f"   □ {c}")


COMMANDS = {"estado": cmd_estado, "plan": cmd_plan, "iniciar": cmd_iniciar, "avanzar": cmd_avanzar,
            "hito": cmd_hito, "etapas": cmd_etapas}


def main(argv):
    cmd = argv[0] if argv else "estado"
    if cmd in ("-h", "--help", "ayuda"):
        print(__doc__)
        return
    if cmd not in COMMANDS:
        sys.exit(f"Comando desconocido «{cmd}». Comandos: {', '.join(COMMANDS)}")
    COMMANDS[cmd](argv[1:])


if __name__ == "__main__":
    main(sys.argv[1:])
