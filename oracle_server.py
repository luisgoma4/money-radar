"""money-radar: servidor local del oráculo por voz.

Sirve el dashboard y una API solo para 127.0.0.1:
  GET  /api/health              {local, claude}
  GET  /api/state               payload del dashboard (para refrescar tras una acción)
  POST /api/converse            {text, context} -> {say, view, options, action?, ...}
                                1) intents locales (oracle_actions.intent); 2) si no, Claude Code headless
  POST /api/action              {id, args} -> {summary, token}   (vista previa, no ejecuta)
  POST /api/action/confirm      {token} -> {ok, message}          (ejecuta; token de un uso, 2 min)

Uso:  python3 -I oracle_server.py [--port 8000]
Claude: `claude -p` con herramientas solo de lectura y salida estructurada; modelo por
ORACLE_MODEL (por defecto sonnet). Usa tu sesión de Claude Code (se quita ANTHROPIC_API_KEY
del entorno; si falla, se reintenta con la clave).
"""

import http.server
import importlib.util
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("oracle_actions", HERE / "oracle_actions.py")
actions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(actions)

PORT = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8000
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
CLAUDE = shutil.which("claude")
MODEL = os.environ.get("ORACLE_MODEL", "sonnet")
TOKENS, LOCK, MUTATE = {}, threading.Lock(), threading.Lock()
READ_TOOLS = ",".join([
    "Read", "Grep", "Glob",
    "Bash(python3 -I causal.py:*)", "Bash(python3 -I orchestrator.py estado:*)", "Bash(python3 -I orchestrator.py plan:*)",
    "Bash(python3 -I oraculo.py lectura:*)", "Bash(python3 -I oraculo.py historial:*)", "Bash(python3 -I oraculo.py)",
    "Bash(python3 -I orchestrator.py)"])
SCHEMA = json.dumps({
    "type": "object", "required": ["say"],
    "properties": {
        "say": {"type": "string"},
        "view": {"type": "object", "properties": {
            "page": {"type": "string", "enum": ["dashboard", "espacio"]},
            "tab": {"type": "string", "enum": ["radar", "strategy", "calendar", "graph", "oracle", "west", "repo"]},
            "mode": {"type": "string"}, "camera": {"type": "string", "enum": ["in", "out", "spin", "stop"]},
            "path": {"type": "array", "items": {"type": "string"}},
            "focus": {"type": "object", "properties": {"kind": {"type": "string"}, "id": {"type": "string"}}}}},
        "options": {"type": "array", "items": {"type": "object", "required": ["label"], "properties": {
            "label": {"type": "string"}, "utterance": {"type": "string"},
            "action": {"type": "object", "properties": {"id": {"type": "string"}, "args": {"type": "object"}}}}}},
        "action": {"type": "object", "properties": {"id": {"type": "string"}, "args": {"type": "object"}}}}})


def ask_claude(text, ctx):
    if not CLAUDE:
        return {"say": "No encuentro Claude Code en este equipo; solo entiendo órdenes conocidas.", "options": []}
    st = actions.state()
    o = st["plan"]["oracle"]
    brief = {"pagina": ctx.get("page"), "pestaña_actual": ctx.get("tab"), "foco": ctx.get("focus"), "ruta": ctx.get("path"),
             "ceremonia": ctx.get("ceremony"),
             "dictamen": o["verdict"], "nave": (o.get("ship") or {}).get("name"),
             "ranking": [st["plan"]["plans"][i]["name"] + f" ({st['plan']['plans'][i]['grade']})" for i in st["plan"]["ranking"][:6]]}
    prompt = f"Contexto de la web (JSON): {json.dumps(brief, ensure_ascii=False)}\n\nEl usuario dice: «{text}»"
    cmd = [CLAUDE, "-p", prompt, "--model", MODEL, "--output-format", "json", "--json-schema", SCHEMA,
           "--append-system-prompt-file", str(HERE / "oracle_prompt.md"), "--allowedTools", READ_TOOLS]
    if ctx.get("session"):
        cmd += ["--resume", ctx["session"]]
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    for attempt_env in (env, dict(os.environ)):
        try:
            r = subprocess.run(cmd, cwd=HERE, env=attempt_env, capture_output=True, text=True, timeout=90)
        except subprocess.TimeoutExpired:
            return {"say": "Estoy tardando demasiado en pensar. Repite la pregunta más concreta.", "options": []}
        try:
            d = json.loads(r.stdout)
        except json.JSONDecodeError:
            d = {"is_error": True, "result": (r.stderr or r.stdout)[-300:]}
        if not d.get("is_error"):
            break
    if d.get("is_error"):
        return {"say": "No he podido consultar a Claude: " + str(d.get("result", ""))[:160], "options": []}
    out = d.get("structured_output") or {}
    if not out:
        try:
            out = json.loads(d.get("result") or "{}")
        except json.JSONDecodeError:
            out = {"say": d.get("result", "")}
    out.setdefault("options", [])
    out["session"] = d.get("session_id")
    out["source"] = "claude"
    for opt in out["options"]:  # drop options whose action is not in the registry
        if opt.get("action") and actions.validate(opt["action"].get("id"), opt["action"].get("args") or {}):
            opt.pop("action")
    if out.get("action") and actions.validate(out["action"].get("id"), out["action"].get("args") or {}):
        out.pop("action")
    return out


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(HERE), **kw)

    def log_message(self, fmt, *args):
        if "/api/" in str(args[0] if args else ""):
            sys.stderr.write("oraculo: " + (fmt % args) + "\n")

    def _origin_ok(self):
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        if host not in ALLOWED_HOSTS:
            return False
        return origin is None or urlparse(origin).netloc in ALLOWED_HOSTS

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 200_000:
            raise ValueError("cuerpo demasiado grande")
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            if not self._origin_ok():
                return self._json(403, {"error": "solo local"})
            if path == "/api/health":
                return self._json(200, {"local": True, "claude": bool(CLAUDE), "model": MODEL})
            if path == "/api/state":
                return self._json(200, actions.state())
            return self._json(404, {"error": "no existe"})
        if path.startswith(("/solicitudes", "/.git", "/.claude")):
            return self._json(404, {"error": "no existe"})
        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        if not self._origin_ok():
            return self._json(403, {"error": "solo local"})
        try:
            body = self._body()
        except (ValueError, json.JSONDecodeError) as e:
            return self._json(400, {"error": str(e)})
        if path == "/api/converse":
            text = (body.get("text") or "").strip()
            if not text:
                return self._json(400, {"error": "texto vacío"})
            ctx = body.get("context") or {}
            st = actions.state()
            res = actions.intent(text, ctx, st)
            return self._json(200, res or ask_claude(text, ctx))
        if path == "/api/action":
            aid, args = body.get("id"), body.get("args") or {}
            err = actions.validate(aid, args)
            if err:
                return self._json(400, {"error": err})
            try:
                summary = actions.ACTIONS[aid]["preview"](args, actions.state())
            except Exception as e:  # bad args (unknown id, etc.)
                return self._json(400, {"error": f"No puedo preparar esa acción: {e}"})
            token = secrets.token_urlsafe(16)
            with LOCK:
                now = time.time()
                for k in [k for k, v in TOKENS.items() if v[2] < now]:
                    TOKENS.pop(k)
                TOKENS[token] = (aid, args, now + 120)
            return self._json(200, {"summary": summary, "token": token})
        if path == "/api/action/confirm":
            with LOCK:
                entry = TOKENS.pop(body.get("token") or "", None)
            if not entry or entry[2] < time.time():
                return self._json(400, {"error": "Confirmación caducada o ya usada. Vuelve a pedir la acción."})
            aid, args, _ = entry
            with MUTATE:
                try:
                    msg = actions.ACTIONS[aid]["run"](args)
                    return self._json(200, {"ok": True, "message": msg or "Hecho."})
                except Exception as e:
                    return self._json(200, {"ok": False, "message": f"No se ha aplicado: {e}"})
        return self._json(404, {"error": "no existe"})


def main():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Oráculo en http://127.0.0.1:{PORT}/dashboard.html  (Claude: {'sí' if CLAUDE else 'no'}, modelo {MODEL})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
