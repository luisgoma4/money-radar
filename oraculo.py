"""money-radar: el oráculo y su ceremonia.

La ceremonia es la reunión periódica del ecosistema (cada strategy.json -> ceremony.cadence_days
días) que dirige Claude como oráculo con el skill /ceremonia. El oráculo lee el plan y el grafo
causal, plantea una pregunta a cada cara y propone un dictamen; las decisiones y compromisos se
registran aquí y aparecen en la pestaña Oráculo del dashboard.

Uso:
  python3 -I oraculo.py                       # lectura del oráculo + fases de la ceremonia
  python3 -I oraculo.py registrar --resumen "…" --decision "…" [--decision "…"] \\
        --compromiso "rol|tarea|AAAA-MM-DD" [--compromiso …] [--proxima AAAA-MM-DD]
  python3 -I oraculo.py historial

Compromisos por rol o cara (p. ej. «SEMF», «Fundación», «Causality»), nunca con nombres:
el repositorio es público. Las ceremonias no se borran.
"""

import datetime as dt
import importlib.util
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TODAY = dt.date.today()
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def context():
    spec = importlib.util.spec_from_file_location("planner", HERE / "planner.py")
    planner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(planner)
    conn = sqlite3.connect(HERE / "money.db")
    conn.row_factory = sqlite3.Row
    opps = [dict(r) for r in conn.execute("SELECT * FROM opportunities")]
    apps = [dict(r) for r in conn.execute("SELECT * FROM applications")]
    cers = [dict(r, decisions=json.loads(r["decisions"]), commitments=json.loads(r["commitments"]))
            for r in conn.execute("SELECT * FROM ceremonies ORDER BY held_on DESC, id DESC")]
    plan = planner.make_plan(opps, planner.load_strategy(), apps, TODAY, cers)
    return conn, plan


def fmt(d):
    return dt.date.fromisoformat(d).strftime("%d/%m/%Y") if d else "—"


def lectura():
    _, plan = context()
    o = plan["oracle"]
    print("LECTURA DEL ORÁCULO · " + fmt(TODAY.isoformat()))
    print(f"Próxima ceremonia: {fmt(o['next_on'])}{'  ← toca ya' if o['due'] else ''}\n")
    if o["ship"]:
        s = o["ship"]
        print(f"Nave en el horizonte: {s['name']} (grado {s['grade']})" + ("  ¡TARDE!" if s["when"] == "late" else ""))
        print("   camino más fuerte: " + " → ".join(s["path"]))
        if s["pending"]:
            print("   bloqueada por: " + ", ".join(s["pending"]))
    if o["milestone"]:
        m = o["milestone"]
        print(f"Hito que más puertas abre: {m['name']} (~{m['weeks']} semanas, {m['blocked']} convocatorias)")
    if o["mediator"]:
        print(f"Mediador clave: {o['mediator']['label']} (por él pasa el mayor flujo de valor hacia las convocatorias)")
    if o["confounder"]:
        c = o["confounder"]
        print(f"Confusor a vigilar: {c['label']} (causa común de {c['calls']} convocatorias)")
    if o["late"]:
        print("Atrasos: " + "; ".join(o["late"]))
    print("\nPreguntas a cada cara:")
    for q in o["questions"]:
        print(f"   {q['name']}: {q['question']}")
    print(f"\nDictamen propuesto: {o['verdict']}")
    print("\nFases de la ceremonia:")
    for i, ph in enumerate(o["phases"], 1):
        print(f"   {i}. {ph['name']} — {ph['detail']}")


def opts(args, name):
    out, i = [], 0
    while i < len(args):
        if args[i] == name and i + 1 < len(args):
            out.append(args[i + 1])
            i += 2
        else:
            i += 1
    return out


def registrar(args):
    decisions = opts(args, "--decision")
    if not decisions:
        sys.exit('Hace falta al menos una --decision "…"')
    commitments = []
    for c in opts(args, "--compromiso"):
        parts = [x.strip() for x in c.split("|")]
        if len(parts) != 3 or not ISO.match(parts[2]):
            sys.exit(f'Compromiso mal formado: «{c}». Formato: "rol|tarea|AAAA-MM-DD"')
        commitments.append({"rol": parts[0], "tarea": parts[1], "fecha": parts[2]})
    nxt = (opts(args, "--proxima") or [None])[0]
    if nxt and not ISO.match(nxt):
        sys.exit("--proxima debe ser AAAA-MM-DD")
    summary = (opts(args, "--resumen") or [None])[0]
    conn, plan = context()
    if not nxt:
        nxt = (TODAY + dt.timedelta(days=plan["oracle"]["cadence_days"])).isoformat()
    conn.execute("INSERT INTO ceremonies (held_on, summary, decisions, commitments, next_on) VALUES (?, ?, ?, ?, ?)",
                 (TODAY.isoformat(), summary, json.dumps(decisions, ensure_ascii=False),
                  json.dumps(commitments, ensure_ascii=False), nxt))
    conn.execute("INSERT INTO changes VALUES ('ceremonia', 'ceremony', NULL, ?, ?)",
                 (f"{len(decisions)} decisiones, {len(commitments)} compromisos", TODAY.isoformat()))
    conn.commit()
    conn.close()
    subprocess.run([sys.executable, "-I", str(HERE / "build.py")], check=True, stdout=subprocess.DEVNULL)
    print(f"Ceremonia registrada ({len(decisions)} decisiones, {len(commitments)} compromisos). Próxima: {fmt(nxt)}")


def historial():
    conn, _ = context()
    rows = conn.execute("SELECT * FROM ceremonies ORDER BY held_on DESC, id DESC").fetchall()
    if not rows:
        print("Aún no hay ceremonias registradas.")
    for r in rows:
        print(f"{fmt(r['held_on'])} · próxima {fmt(r['next_on'])}" + (f" · {r['summary']}" if r["summary"] else ""))
        for d in json.loads(r["decisions"]):
            print(f"   ✓ {d}")
        for c in json.loads(r["commitments"]):
            print(f"   → {c['rol']}: {c['tarea']} ({fmt(c['fecha'])})")


def main(argv):
    cmd = argv[0] if argv else "lectura"
    if cmd == "lectura":
        lectura()
    elif cmd == "registrar":
        registrar(argv[1:])
    elif cmd == "historial":
        historial()
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
