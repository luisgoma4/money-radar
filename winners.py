"""money-radar: summarise who won a call, from the public BDNS (infosubvenciones) API.

Usage:  python3 -I winners.py <BDNS call number> [<BDNS call number> ...]
        python3 -I winners.py --find "<words in the call title>"   # list matching calls + numbers

Prints award count, total, median and range, beneficiary types, and the largest
awards to legal entities. Individuals are masked by BDNS and never printed.
Paste the useful part into the row's `winners` field with the source and date.
If a call has no awards in BDNS, the funder may publish them elsewhere (resolution
PDF, council decision) — say so in `winners` instead of leaving it empty.
"""

import json
import statistics
import sys
import urllib.parse
import urllib.request
from collections import defaultdict

API = "https://www.infosubvenciones.es/bdnstrans/api"
TYPES = [("UNIVERS", "university"), ("FUNDACI", "foundation"), ("ASOCIACI", "association"),
         ("AYUNTAM", "council"), ("CONSEJO SUPERIOR", "CSIC"), (" S.L", "company"),
         ("SOCIEDAD LIMITADA", "company"), (" S.A", "company"), ("COOPERATIVA", "cooperative")]


def get(path, **params):
    url = f"{API}/{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)


def kind(name):
    up = name.upper()
    return next((t for k, t in TYPES if k in up), "other")


def find(words):
    for c in get("convocatorias/busqueda", descripcion=words, page=0, pageSize=30).get("content", []):
        print(c.get("numeroConvocatoria"), c.get("fechaRecepcion"), c.get("nivel2"), "|", (c.get("descripcion") or "")[:100])


def summarise(num):
    awards, page = [], 0
    while True:
        d = get("concesiones/busqueda", numeroConvocatoria=num, page=page, pageSize=500)
        awards += d.get("content", [])
        if d.get("last", True):
            break
        page += 1
    try:
        call = get("convocatorias", numConv=num)
        title = (call.get("descripcion") or "")[:120]
        organ = (call.get("organo") or {}).get("nivel3")
    except Exception:
        title, organ = "", ""
    print(f"== BDNS {num} · {organ} · {title}")
    if not awards:
        print("   no awards published in BDNS (check the funder's resolution instead)")
        return
    amounts = [a["importe"] for a in awards]
    dates = sorted({a["fechaConcesion"] for a in awards})
    print(f"   {len(awards)} awards · total EUR {sum(amounts):,.0f} · median {statistics.median(amounts):,.0f}"
          f" · range {min(amounts):,.0f}–{max(amounts):,.0f} · awarded {dates[0]}..{dates[-1]}")
    by = defaultdict(list)
    for a in awards:
        by["individual" if a["beneficiario"].startswith("*") else kind(a["beneficiario"])].append(a["importe"])
    print("   by type: " + "; ".join(f"{k} {len(v)} (median {statistics.median(v):,.0f})"
                                   for k, v in sorted(by.items(), key=lambda kv: -len(kv[1]))))
    entities = [a for a in awards if not a["beneficiario"].startswith("*")]
    for a in sorted(entities, key=lambda a: -a["importe"])[:15]:
        print(f"   {a['importe']:>12,.0f}  {a['beneficiario'].split(' ', 1)[-1][:80]}")


def main(argv):
    if not argv:
        sys.exit(__doc__)
    if argv[0] == "--find":
        find(" ".join(argv[1:]))
        return
    for num in argv:
        summarise(num)


if __name__ == "__main__":
    main(sys.argv[1:])
