"""money-radar: maintenance pass over money.db, run before build.py.

- Marks opportunities whose fixed deadline has passed as 'closed' (logged in `changes`);
  build.py then archives them. Entries are never deleted, only archived.
- Lists archived entries whose review date has come (recover them or re-date the review),
  and warns about DB entries missing from ROWS (build.py archives them, never deletes).
- Checks build.py's ROWS for data-quality problems and exits non-zero if any are found,
  so a bad edit stops the run before the dashboard is rebuilt.

Run with:  python3 -I monitor.py && python3 -I build.py
"""

import ast
import datetime as dt
import re
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = HERE / "money.db"
TODAY = dt.date.today()
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")

LINES = {"cloudy", "semf", "causality", "branchout", "delfina"}
KINDS = {"grant", "loan", "equity", "prize", "network"}
SCOPES = {"Madrid West", "Madrid", "Spain", "EU", "International"}
DEADLINE_KINDS = {"fixed", "rolling", "expected"}
VERIFICATION = {"verified", "unverified"}
REQUIRED = ["id", "name", "funder", "kind", "scope", "lines", "deadline_kind", "verification", "url", "source"]


def close_passed(conn):
    today = TODAY.isoformat()
    rows = conn.execute(
        "SELECT id, status FROM opportunities "
        "WHERE deadline_kind = 'fixed' AND deadline < ? AND status != 'closed'",
        (today,),
    ).fetchall()
    for oid, status in rows:
        conn.execute("UPDATE opportunities SET status = 'closed' WHERE id = ?", (oid,))
        conn.execute("INSERT INTO changes VALUES (?, 'status', ?, 'closed', ?)", (oid, status, today))
        print(f"closed: {oid} (deadline passed)")
    conn.commit()
    return len(rows)


def read_rows():
    """Parse ROWS out of build.py without importing it (-I drops the script dir from sys.path)."""
    tree = ast.parse((HERE / "build.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "ROWS" for t in node.targets):
            return [{kw.arg: ast.literal_eval(kw.value) for kw in call.keywords} for call in node.value.elts]
    raise SystemExit("monitor: ROWS not found in build.py")


def check(rows):
    problems, seen = [], set()
    for r in rows:
        rid = r.get("id", "?")
        for f in REQUIRED:
            if not r.get(f):
                problems.append(f"{rid}: missing {f}")
        if rid in seen:
            problems.append(f"{rid}: duplicate id")
        seen.add(rid)
        if not set(str(r.get("lines", "")).split(",")) <= LINES:
            problems.append(f"{rid}: unknown line in {r.get('lines')!r}")
        for field, allowed in (("kind", KINDS), ("scope", SCOPES),
                               ("deadline_kind", DEADLINE_KINDS), ("verification", VERIFICATION)):
            if r.get(field) not in allowed:
                problems.append(f"{rid}: {field}={r.get(field)!r} not in {sorted(allowed)}")
        for f in ("opens", "deadline"):
            if r.get(f) is not None and not ISO.match(str(r[f])):
                problems.append(f"{rid}: {f} must be YYYY-MM-DD")
        if r.get("deadline_kind") == "fixed" and not r.get("deadline"):
            problems.append(f"{rid}: fixed deadline_kind needs a deadline")
        if r.get("deadline_kind") in ("rolling", "expected") and r.get("deadline"):
            problems.append(f"{rid}: {r['deadline_kind']} rows must not carry a deadline")
        lo, hi = r.get("amount_min"), r.get("amount_max")
        if lo is not None and hi is not None and lo > hi:
            problems.append(f"{rid}: amount_min > amount_max")
        if not str(r.get("url", "")).startswith("https://"):
            problems.append(f"{rid}: url must be https")
        past = r.get("deadline_kind") == "fixed" and r.get("deadline") and ISO.match(str(r["deadline"])) \
            and dt.date.fromisoformat(r["deadline"]) < TODAY
        if past and not r.get("winners"):
            problems.append(f"{rid}: closed call needs `winners` (past winners/award stats, or 'pending' + when)")
        if r.get("archived") and not (r.get("archive_reason") and r.get("review_on")):
            problems.append(f"{rid}: archived rows need archive_reason and review_on")
        if r.get("review_on") is not None and not ISO.match(str(r["review_on"])):
            problems.append(f"{rid}: review_on must be YYYY-MM-DD")
    return problems


def archive_report(conn, rows):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(opportunities)")}
    if "archived" not in cols:
        return  # pre-archive database; build.py migrates it
    ids = {r["id"] for r in rows}
    for (oid,) in conn.execute("SELECT id FROM opportunities WHERE archived = 0"):
        if oid not in ids:
            print(f"monitor: {oid} is in money.db but not in ROWS; build.py will archive it (never delete)")
    due = conn.execute(
        "SELECT id, review_on, archive_reason FROM opportunities "
        "WHERE archived = 1 AND review_on <= ? ORDER BY review_on", (TODAY.isoformat(),)).fetchall()
    for oid, review_on, reason in due:
        print(f"review due: {oid} (since {review_on}; {reason})")


def main():
    rows = read_rows()
    problems = check(rows)
    if DB.exists():
        conn = sqlite3.connect(DB)
        closed = close_passed(conn)
        archive_report(conn, rows)
        conn.close()
    else:
        closed = 0
        print("monitor: money.db not found yet — build.py will create it")
    if problems:
        print("monitor: data problems in build.py ROWS:", *problems, sep="\n  ")
        sys.exit(1)
    print(f"monitor: rows OK; {closed} closed today")


if __name__ == "__main__":
    main()
