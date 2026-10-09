"""money-radar: análisis causal del grafo del ecosistema (el mismo que dibujan el 2D y el 3D).

El grafo es un DAG: dirección = causa -> efecto, peso 0-1 = fuerza de la contribución
(juicio de planificación). Los efectos se calculan con las reglas de Wright: el peso
de un camino es el producto de sus aristas y el efecto total, la suma de los caminos.

Uso:
  python3 -I causal.py                       # resumen: DAG, mediadores y confusores del ecosistema
  python3 -I causal.py nodos [texto]         # ids y etiquetas (filtra por texto)
  python3 -I causal.py relaciones <id>       # aristas que entran y salen de un nodo, con pesos
  python3 -I causal.py analiza <T> <Y>       # efecto de T sobre Y: caminos, mediadores, confusores, ajuste

Ids: f_* caras, e_* figuras legales, p_* productos, x_* socios, z_* factores,
m_<HITO> hitos, c_<id> convocatorias (ships).
"""

import datetime as dt
import importlib.util
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_plan():
    spec = importlib.util.spec_from_file_location("planner", HERE / "planner.py")
    planner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(planner)
    conn = sqlite3.connect(HERE / "money.db")
    conn.row_factory = sqlite3.Row
    opps = [dict(r) for r in conn.execute("SELECT * FROM opportunities")]
    apps = [dict(r) for r in conn.execute("SELECT * FROM applications")]
    conn.close()
    plan = planner.make_plan(opps, planner.load_strategy(), apps, dt.date.today())
    return planner, plan


def main(argv):
    planner, plan = load_plan()
    eco = plan["ecosystem"]
    label = {n["id"]: n["label"] for n in eco["nodes"]}
    kind = {n["id"]: n["kind"] for n in eco["nodes"]}
    cmd = argv[0] if argv else "resumen"

    def need(i):
        if i not in label:
            sys.exit(f"Nodo desconocido «{i}». Usa: causal.py nodos <texto>")
        return i

    if cmd == "resumen":
        c = plan["causal"]
        by = {}
        for n in eco["nodes"]:
            by[n["kind"]] = by.get(n["kind"], 0) + 1
        print(f"Grafo: {len(eco['nodes'])} nodos, {len(eco['edges'])} relaciones · DAG: {'sí' if c['dag'] else 'NO, ciclo en ' + ', '.join(c['cycle'])}")
        print("Por tipo: " + ", ".join(f"{k} {v}" for k, v in sorted(by.items())))
        print("\nMediadores (flujo ponderado hacia todas las convocatorias, 1 = máximo):")
        for m in c["mediators"]:
            print(f"   {m['flow']:.2f}  {m['label']} [{m['id']}, {m['kind']}]")
        print("\nPosibles confusores (causa común de varias convocatorias):")
        for z in c["confounders"]:
            print(f"   {z['label']} [{z['id']}, {z['kind']}] → {z['calls']} convocatorias, {z['children']} hijos")
    elif cmd == "nodos":
        q = " ".join(argv[1:]).lower()
        for n in eco["nodes"]:
            if q in (n["id"] + " " + n["label"]).lower():
                print(f"{n['id']:<45} {n['kind']:<10} {n['label']}")
    elif cmd == "relaciones":
        i = need(argv[1] if len(argv) > 1 else "")
        print(f"{label[i]} [{i}, {kind[i]}]")
        for e in eco["edges"]:
            if e["to"] == i:
                print(f"   ← {e['weight']:.2f}  {label[e['from']]} · {e['label']} ({e['status']})")
        for e in eco["edges"]:
            if e["from"] == i:
                print(f"   → {e['weight']:.2f}  {label[e['to']]} · {e['label']} ({e['status']})")
    elif cmd == "analiza":
        if len(argv) < 3:
            sys.exit("Uso: analiza <tratamiento> <resultado>")
        t, y = need(argv[1]), need(argv[2])
        a = planner.analyze(eco, t, y)
        print(f"Efecto de «{label[t]}» sobre «{label[y]}»")
        print(f"   total {a['total']:.3f} = directo {a['direct']:.3f} + indirecto {a['indirect']:.3f} · {a['n_paths']} caminos")
        for path, w in a["paths"][:8]:
            print(f"   {w:.3f}  " + " → ".join(label[n] for n in path))
        if a["mediators"]:
            print("Mediadores (peso de los caminos que pasan por ellos):")
            for n, w in a["mediators"][:8]:
                print(f"   {w:.3f}  {label[n]} [{n}]")
        print("Confusores (causas comunes de T e Y que no dependen de T): "
              + (", ".join(f"{label[z]} [{z}]" for z in a["confounders"]) or "ninguno"))
        print("Conjunto de ajuste por la puerta trasera (padres de T que llegan a Y): "
              + (", ".join(f"{label[z]} [{z}]" for z in a["adjust"]) or "vacío: no hace falta ajustar"))
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
